"""
Public-facing web app — hosted separately from the bot (e.g. on EC2).
Holds no game data itself; every page fetches from the internal API
(webapi.py, running on the bot's machine) using the session token
issued at /login, forwarded as a cookie between browser and this app.

Run with: uvicorn web.main:app --host 0.0.0.0 --port 8080

CD test marker: deploy-web.yml pipeline
"""
import asyncio
import os
import json
import logging
from contextlib import asynccontextmanager

import hashlib
import re
import io
import httpx

# Production runs "uvicorn web.main:app" from the repo root; local runs from
# inside web/ use "main:app". Support both.
try:
    from web import badge_svg
except ImportError:
    import badge_svg
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

log = logging.getLogger("web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
API_WS_BASE_URL = API_BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
# Battle music is hosted in S3 rather than the repo (see battle_audio.js).
AUDIO_BASE_URL = os.getenv("AUDIO_BASE_URL", "https://poke-music.s3.us-east-1.amazonaws.com").rstrip("/")
SESSION_COOKIE = "session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60  # 7 days, matches the API's JWT expiry

BASE_DIR = os.path.dirname(__file__)

# A fresh httpx.AsyncClient() per request pays a full new TCP+TLS handshake to
# the internal API on every single call — that's the dominant cost of a page
# load when this app and the API are on different machines. One shared,
# connection-pooled client (created at startup, reused for the process's
# lifetime) turns that into one handshake and cheap keep-alive requests after.
http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(
        timeout=15.0, limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
    )
    yield
    await http_client.aclose()


app = FastAPI(title="ShawtyBot Pokémon Web", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["AUDIO_BASE_URL"] = AUDIO_BASE_URL


# Templates load CSS/JS through static_url(), which appends a fingerprint of
# the file's contents (/static/style.css?v=3f9a...). A deploy that changes a
# file changes its URL, so browsers can cache these for a year and still pick
# up every update immediately — no revalidation round trip per file per page
# (which was the old "no-cache" behavior) and no hard refresh needed.
_static_versions: dict[str, str] = {}


def static_url(path: str) -> str:
    version = _static_versions.get(path)
    if version is None:
        try:
            with open(os.path.join(BASE_DIR, "static", path), "rb") as f:
                version = hashlib.md5(f.read()).hexdigest()[:10]
        except OSError:
            version = ""
        _static_versions[path] = version
    return f"/static/{path}?v={version}" if version else f"/static/{path}"


templates.env.globals["static_url"] = static_url


# ---------- Artwork thumbnails ----------
# The official artwork is a ~120-150 KB, 475px PNG per Pokémon, hotlinked from
# GitHub — and grids (collection, Pokédex, pickers, battle roster icons) show
# it at ~100px, so a long collection page pulled tens of MB. /img/art/{id}.webp
# fetches it once, shrinks it to a small WebP (~5-10 KB), keeps it on disk
# and serves it with a year-long cache. Detail views keep the full art.
ART_SOURCE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/"
ART_URL_RE = re.compile(r"^" + re.escape(ART_SOURCE) + r"((?:shiny/)?\d{1,5})\.png$")
THUMB_DIR = os.path.join(BASE_DIR, ".thumbs")
THUMB_SIZE = 200
_thumb_locks: dict[str, asyncio.Lock] = {}


def art_thumb(url: str | None) -> str | None:
    """The thumbnail URL for an official-artwork URL; anything else as is."""
    m = ART_URL_RE.match(url or "")
    return f"/img/art/{m.group(1)}.webp" if m else url


templates.env.filters["thumb"] = art_thumb


def _make_thumb(png: bytes, dest: str):
    from PIL import Image  # imported lazily: the app still runs without Pillow
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    im.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
    tmp = dest + ".tmp"
    im.save(tmp, "WEBP", quality=82, method=4)
    os.replace(tmp, dest)


@app.get("/img/art/{name:path}")
async def artwork_thumbnail(name: str):
    m = re.fullmatch(r"((?:shiny/)?)(\d{1,5})\.webp", name)
    if not m:
        return Response(status_code=404)
    source = f"{ART_SOURCE}{m.group(1)}{m.group(2)}.png"
    dest = os.path.join(THUMB_DIR, f"{'shiny_' if m.group(1) else ''}{m.group(2)}.webp")
    if not os.path.exists(dest):
        lock = _thumb_locks.setdefault(dest, asyncio.Lock())
        async with lock:
            if not os.path.exists(dest):
                try:
                    resp = await http_client.get(source)
                    if resp.status_code != 200:
                        return Response(status_code=404)
                    os.makedirs(THUMB_DIR, exist_ok=True)
                    await asyncio.to_thread(_make_thumb, resp.content, dest)
                except Exception as e:  # noqa: BLE001 - never break an image
                    log.warning(f"Thumbnail for {name} failed ({e}); serving the original")
                    return RedirectResponse(source)
    return FileResponse(dest, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.middleware("http")
async def static_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        if request.query_params.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # Unversioned references (images loaded from JS/CSS): revalidate so
            # a redeploy is picked up; ETag makes that a cheap 304.
            response.headers["Cache-Control"] = "no-cache"
    return response


async def api_get(session: str, path: str) -> tuple[int, dict | list]:
    resp = await http_client.get(f"{API_BASE_URL}{path}", headers={"Authorization": f"Bearer {session}"})
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {}


async def api_post(session: str, path: str, payload: dict) -> tuple[int, dict | list]:
    resp = await http_client.post(
        f"{API_BASE_URL}{path}", json=payload, headers={"Authorization": f"Bearer {session}"}
    )
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {}


def clear_session(response):
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/")
def landing(request: Request):
    if request.cookies.get(SESSION_COOKIE):
        return RedirectResponse("/profile")
    return templates.TemplateResponse(request, "landing.html")


@app.get("/login")
async def login(request: Request, token: str):
    resp = await http_client.post(f"{API_BASE_URL}/api/auth/exchange", json={"token": token})

    if resp.status_code != 200:
        detail = resp.json().get("detail", "This link is invalid or has expired.")
        return templates.TemplateResponse(request, "landing.html", {"error": detail})

    session = resp.json()["session"]
    response = RedirectResponse("/profile")
    response.set_cookie(
        SESSION_COOKIE, session,
        max_age=SESSION_MAX_AGE, httponly=True, secure=COOKIE_SECURE, samesite="lax",
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/")
    return clear_session(response)


@app.get("/profile")
async def profile(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    (status, data), (_, achievements) = await asyncio.gather(
        api_get(session, "/api/me"), api_get(session, "/api/achievements")
    )
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(
        request, "profile.html", {"trainer": data, "achievements": achievements, "is_own": True}
    )


@app.get("/trainers")
async def trainers_directory(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/trainers")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "trainers.html", {"trainers": data})


@app.get("/rankings")
async def rankings(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/rankings")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "rankings.html", {"rankings": data})


@app.get("/trainer/{target_id}")
async def view_trainer(request: Request, target_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    (status, data), (_, achievements) = await asyncio.gather(
        api_get(session, f"/api/trainer/{target_id}"),
        api_get(session, f"/api/trainer/{target_id}/achievements"),
    )
    if status == 401:
        return clear_session(RedirectResponse("/"))
    if status == 404:
        return templates.TemplateResponse(request, "landing.html", {"error": "That trainer doesn't exist."})

    return templates.TemplateResponse(
        request, "profile.html", {"trainer": data, "achievements": achievements, "is_own": False}
    )


@app.get("/pokedex")
async def pokedex(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/pokedex/full")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "pokedex.html", {"species": data})


@app.get("/inventory")
async def inventory(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/inventory")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "inventory.html", {"inventory": data})


@app.get("/team")
async def team(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/team")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "team.html", {"team": data, "team_json": json.dumps(data)})


@app.get("/store")
async def store(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/store")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "store.html", {"store": data})


@app.get("/collection")
async def collection(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/collection-individuals")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "collection.html", {"collection": data})


@app.get("/battles")
async def battles(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    return templates.TemplateResponse(request, "battles.html", {})


@app.get("/battles/{battle_id}")
async def battle_room(request: Request, battle_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, f"/api/battles/{battle_id}")
    if status == 401:
        return clear_session(RedirectResponse("/"))
    if status == 404:
        return templates.TemplateResponse(request, "landing.html", {"error": "That battle doesn't exist."})

    return templates.TemplateResponse(
        request, "battle_room.html", {"battle": data, "battle_id": battle_id, "battle_json": json.dumps(data)}
    )


@app.get("/gyms")
async def gyms(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/gyms")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    regions = data.get("regions", [])
    all_gyms = [g for group in regions for g in group["gyms"]]
    earned_count = sum(1 for g in all_gyms if g["earned"])
    _, custom = await api_get(session, "/api/custom-gyms")
    return templates.TemplateResponse(
        request, "gyms.html",
        {"regions": regions, "earned_count": earned_count, "total_count": len(all_gyms), "custom": custom or {}},
    )


@app.get("/gyms/mine")
async def my_gym(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/my-gym")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "custom_gym.html", {"my_gym": data})


# ---------- Custom gym badges (SVG, drawn by badge_svg.py) ----------

SVG_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}


@app.get("/custom-badges/preview.svg")
def custom_badge_preview(shape: str = "", primary: str = "", secondary: str = "", emblem: str = ""):
    """Live preview for the gym builder's badge designer (no data stored)."""
    svg = badge_svg.render({"shape": shape, "primary": primary, "secondary": secondary, "emblem": emblem})
    return Response(svg, media_type="image/svg+xml", headers={**SVG_HEADERS, "Cache-Control": "public, max-age=86400"})


@app.get("/custom-badges/{owner_id}.svg")
async def custom_badge(request: Request, owner_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    design = None
    if session:
        status, data = await api_get(session, f"/api/custom-gyms/{owner_id}")
        if status == 200:
            design = data.get("badge_design")
    if design is None:
        return Response(status_code=404)
    # Pages link these with ?v=<last update>, so a redesign gets a new URL.
    return Response(badge_svg.render(design), media_type="image/svg+xml",
                    headers={**SVG_HEADERS, "Cache-Control": "private, max-age=3600"})


@app.get("/league")
async def league(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/league")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "league.html", {"league": data})


@app.get("/rewards")
async def rewards(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/rewards")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "rewards.html", {"rewards": data})


@app.get("/trades")
async def trades(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    return templates.TemplateResponse(request, "trades.html", {})


@app.get("/trades/{trade_id}")
async def trade_room(request: Request, trade_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, f"/api/trades/{trade_id}")
    if status == 401:
        return clear_session(RedirectResponse("/"))
    if status == 404:
        return templates.TemplateResponse(request, "landing.html", {"error": "That trade doesn't exist."})

    return templates.TemplateResponse(
        request, "trade_room.html", {"trade": data, "trade_id": trade_id, "trade_json": json.dumps(data)}
    )


# ---------- JSON proxy endpoints for the customize modal's JS ----------
# These exist so client-side JS never sees the internal API's session token
# or hostname directly — it only ever talks to this same-origin app.

@app.get("/api/proxy/inventory")
async def proxy_inventory(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/inventory")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/characters")
async def proxy_characters(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/characters")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/character")
async def proxy_set_character(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, "/api/character", body)
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/pokedex")
async def proxy_pokedex(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/pokedex")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/favorite")
async def proxy_set_favorite(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, "/api/favorite", body)
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/team")
async def proxy_get_team(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/team")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/team")
async def proxy_set_team(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, "/api/team", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/store/buy")
async def proxy_buy_item(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, "/api/store/buy", body)
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/collection/{catch_id}")
async def proxy_collection_detail(request: Request, catch_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/collection/{catch_id}")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/collection/transfer")
async def proxy_transfer_pokemon(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, "/api/collection/transfer", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/collection/{catch_id}/nickname")
async def proxy_set_nickname(request: Request, catch_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/collection/{catch_id}/nickname", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/collection/{catch_id}/convert-candy")
async def proxy_convert_candy(request: Request, catch_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/collection/{catch_id}/convert-candy", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/collection/{catch_id}/evolve")
async def proxy_evolve(request: Request, catch_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/collection/{catch_id}/evolve", body)
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/species/{dex_id}")
async def proxy_species_detail(request: Request, dex_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/species/{dex_id}")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/pokemon-config/{dex_id}")
async def proxy_get_pokemon_config(request: Request, dex_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/pokemon-config/{dex_id}")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/pokemon-config/{dex_id}")
async def proxy_set_pokemon_config(request: Request, dex_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/pokemon-config/{dex_id}", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/{battle_id}/cheer")
async def proxy_cheer_battle(request: Request, battle_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Log in to cheer"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/battles/{battle_id}/cheer", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/pokemon-config/{dex_id}/item")
async def proxy_set_pokemon_held_item(request: Request, dex_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/pokemon-config/{dex_id}/item", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/pokemon-config/{dex_id}/form")
async def proxy_set_pokemon_forme(request: Request, dex_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/pokemon-config/{dex_id}/form", body)
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/battles")
async def proxy_list_battles(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/battles")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/battles/{battle_id}")
async def proxy_get_battle(request: Request, battle_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/battles/{battle_id}")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/gyms")
async def proxy_list_gyms(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/gyms")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/gyms/{gym_key}")
async def proxy_get_gym(request: Request, gym_key: str):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/gyms/{gym_key}")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/gym/{gym_key}")
async def proxy_start_gym_battle(request: Request, gym_key: str):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/battles/gym/{gym_key}", {})
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/custom-gyms/{owner_id}")
async def proxy_custom_gym_detail(request: Request, owner_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/custom-gyms/{owner_id}")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/my-gym")
async def proxy_save_my_gym(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, "/api/my-gym", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/custom-gym/{owner_id}")
async def proxy_start_custom_gym_battle(request: Request, owner_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/battles/custom-gym/{owner_id}", {})
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/league")
async def proxy_get_league(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/league")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/rewards/mega")
async def proxy_claim_mega(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, "/api/rewards/mega", body)
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/rewards")
async def proxy_get_rewards(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/rewards")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/league/{generation}/{member_key}")
async def proxy_get_league_member(request: Request, generation: str, member_key: str):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/league/{generation}/{member_key}")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/elite4/{generation}/{member_key}")
async def proxy_start_elite_four_battle(request: Request, generation: str, member_key: str):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/battles/elite4/{generation}/{member_key}", {})
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/champion/{generation}")
async def proxy_start_champion_battle(request: Request, generation: str):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/battles/champion/{generation}", {})
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/{battle_id}/accept")
async def proxy_accept_battle(request: Request, battle_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/battles/{battle_id}/accept", {})
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/{battle_id}/decline")
async def proxy_decline_battle(request: Request, battle_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/battles/{battle_id}/decline", {})
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/{battle_id}/action")
async def proxy_submit_battle_action(request: Request, battle_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/battles/{battle_id}/action", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/{battle_id}/forced-switch")
async def proxy_submit_forced_switch(request: Request, battle_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/battles/{battle_id}/forced-switch", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/battles/{battle_id}/forfeit")
async def proxy_forfeit_battle(request: Request, battle_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/battles/{battle_id}/forfeit", {})
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/collection")
async def proxy_list_collection(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/collection")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/collection/by-species/{dex_id}")
async def proxy_collection_by_species(request: Request, dex_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/collection/by-species/{dex_id}")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/trainers")
async def proxy_list_trainers(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/trainers")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/trainer/{target_id}/collection")
async def proxy_trainer_collection(request: Request, target_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/trainer/{target_id}/collection")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/collection-individuals")
async def proxy_collection_individuals(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/collection-individuals")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/trainer/{target_id}/collection-individuals")
async def proxy_trainer_collection_individuals(request: Request, target_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/trainer/{target_id}/collection-individuals")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/trainer/{target_id}/collection/by-species/{dex_id}")
async def proxy_trainer_collection_by_species(request: Request, target_id: int, dex_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/trainer/{target_id}/collection/by-species/{dex_id}")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/trades/propose")
async def proxy_propose_trade(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, "/api/trades/propose", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/trades/start/{target_user_id}")
async def proxy_start_trade(request: Request, target_user_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/trades/start/{target_user_id}", {})
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/trades")
async def proxy_list_trades(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, "/api/trades")
    return JSONResponse(data, status_code=status)


@app.get("/api/proxy/trades/{trade_id}")
async def proxy_get_trade(request: Request, trade_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_get(session, f"/api/trades/{trade_id}")
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/trades/{trade_id}/accept")
async def proxy_accept_trade(request: Request, trade_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/trades/{trade_id}/accept", {})
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/trades/{trade_id}/decline")
async def proxy_decline_trade(request: Request, trade_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/trades/{trade_id}/decline", {})
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/trades/{trade_id}/cancel")
async def proxy_cancel_trade(request: Request, trade_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/trades/{trade_id}/cancel", {})
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/trades/{trade_id}/offer")
async def proxy_offer_trade(request: Request, trade_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/trades/{trade_id}/offer", body)
    return JSONResponse(data, status_code=status)


@app.post("/api/proxy/trades/{trade_id}/confirm")
async def proxy_confirm_trade(request: Request, trade_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    status, data = await api_post(session, f"/api/trades/{trade_id}/confirm", {})
    return JSONResponse(data, status_code=status)


# ---------- WebSocket relay ----------
# The browser only ever talks to this same-origin app (auth via its own
# httponly session cookie, exactly like every HTTP route above) — this proxy
# is what actually holds the second, server-to-server WebSocket connection to
# the internal API using that session's token, relaying pushes back down.
# If the reverse proxy in front of either service doesn't yet pass WebSocket
# upgrades through, this connection simply fails to open and battle_room.js
# falls back to polling — nothing breaks, it just isn't instant until that's
# configured (see deploy/README.md).

@app.websocket("/ws/battles/{battle_id}")
async def battle_websocket_relay(websocket: WebSocket, battle_id: int):
    session = websocket.cookies.get(SESSION_COOKIE)
    if not session:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    upstream_url = f"{API_WS_BASE_URL}/ws/battles/{battle_id}?token={session}"

    try:
        async with websockets.connect(upstream_url, open_timeout=10) as upstream:
            async def pump_upstream_to_client():
                async for message in upstream:
                    await websocket.send_text(message)

            async def pump_client_to_upstream():
                # The client never sends real actions over this socket (those
                # go through the regular POST proxy endpoints above) — this
                # just keeps the receive loop alive so we notice a disconnect.
                while True:
                    await websocket.receive_text()

            pump_task = asyncio.ensure_future(pump_upstream_to_client())
            recv_task = asyncio.ensure_future(pump_client_to_upstream())
            try:
                done, pending = await asyncio.wait(
                    {pump_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
            finally:
                pump_task.cancel()
                recv_task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"Battle WebSocket relay closed early for battle {battle_id}: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/trades/{trade_id}")
async def trade_websocket_relay(websocket: WebSocket, trade_id: int):
    session = websocket.cookies.get(SESSION_COOKIE)
    if not session:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    upstream_url = f"{API_WS_BASE_URL}/ws/trades/{trade_id}?token={session}"

    try:
        async with websockets.connect(upstream_url, open_timeout=10) as upstream:
            async def pump_upstream_to_client():
                async for message in upstream:
                    await websocket.send_text(message)

            async def pump_client_to_upstream():
                while True:
                    await websocket.receive_text()

            pump_task = asyncio.ensure_future(pump_upstream_to_client())
            recv_task = asyncio.ensure_future(pump_client_to_upstream())
            try:
                done, pending = await asyncio.wait(
                    {pump_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
            finally:
                pump_task.cancel()
                recv_task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"Trade WebSocket relay closed early for trade {trade_id}: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
