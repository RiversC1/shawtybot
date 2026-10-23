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
from markupsafe import Markup

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

# Appraisal stars (the API's iv_stars, 0-4). Kept in step with
# web/static/collection.js's starsHtml.
IV_STAR_LABELS = {0: "0★ Poor", 1: "1★ Decent", 2: "2★ Good", 3: "3★ Excellent", 4: "4★ Perfect"}


def iv_stars_html(stars) -> Markup:
    stars = int(stars or 0)
    filled = 3 if stars == 4 else stars
    icons = "".join(f'<span class="iv-star{" is-on" if i < filled else ""}">★</span>' for i in range(3))
    return Markup(f'<span class="iv-stars iv-stars-{stars}" aria-label="{IV_STAR_LABELS[stars]}">{icons}</span>')


templates.env.filters["iv_stars_html"] = iv_stars_html
templates.env.filters["iv_stars_label"] = lambda stars: IV_STAR_LABELS[int(stars or 0)]


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


async def load_page(request: Request, *paths: str, not_found: str | None = None):
    """The internal-API data a logged-in page needs (fetched in parallel), or
    the Response to send instead: back to the landing page when logged out or
    the session expired, or the landing page with `not_found` as an error."""
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")
    results = await asyncio.gather(*(api_get(session, p) for p in paths))
    status = results[0][0] if results else 200
    if status == 401:
        return clear_session(RedirectResponse("/"))
    if status == 404 and not_found:
        return templates.TemplateResponse(request, "landing.html", {"error": not_found})
    return [data for _, data in results]


# Pages that render one API response as-is: (URL, API path, template, the
# name the template reads it as).
SIMPLE_PAGES = [
    ("/trainers", "/api/trainers", "trainers.html", "trainers"),
    ("/rankings", "/api/rankings", "rankings.html", "rankings"),
    ("/pokedex", "/api/pokedex/full", "pokedex.html", "species"),
    ("/inventory", "/api/inventory", "inventory.html", "inventory"),
    ("/store", "/api/store", "store.html", "store"),
    ("/collection", "/api/collection-individuals", "collection.html", "collection"),
    ("/gyms/mine", "/api/my-gym", "custom_gym.html", "my_gym"),
    ("/league", "/api/league", "league.html", "league"),
    ("/legends", "/api/legends", "legends.html", "legends"),
    ("/rewards", "/api/rewards", "rewards.html", "rewards"),
]


def _simple_page(api_path: str, template: str, name: str):
    async def page(request: Request):
        loaded = await load_page(request, api_path)
        if isinstance(loaded, Response):
            return loaded
        return templates.TemplateResponse(request, template, {name: loaded[0]})
    return page


for _url, _api_path, _template, _name in SIMPLE_PAGES:
    app.add_api_route(_url, _simple_page(_api_path, _template, _name), methods=["GET"], include_in_schema=False)


@app.get("/profile")
async def profile(request: Request):
    loaded = await load_page(request, "/api/me", "/api/achievements")
    if isinstance(loaded, Response):
        return loaded
    trainer, achievements = loaded
    return templates.TemplateResponse(request, "profile.html", {"trainer": trainer, "achievements": achievements, "is_own": True})


@app.get("/trainer/{target_id}")
async def view_trainer(request: Request, target_id: int):
    loaded = await load_page(request, f"/api/trainer/{target_id}", f"/api/trainer/{target_id}/achievements",
                             not_found="That trainer doesn't exist.")
    if isinstance(loaded, Response):
        return loaded
    trainer, achievements = loaded
    return templates.TemplateResponse(request, "profile.html", {"trainer": trainer, "achievements": achievements, "is_own": False})


@app.get("/team")
async def team(request: Request):
    loaded = await load_page(request, "/api/team")
    if isinstance(loaded, Response):
        return loaded
    return templates.TemplateResponse(request, "team.html", {"team": loaded[0], "team_json": json.dumps(loaded[0])})


@app.get("/battles")
async def battles(request: Request):
    loaded = await load_page(request)
    return loaded if isinstance(loaded, Response) else templates.TemplateResponse(request, "battles.html", {})


@app.get("/battles/{battle_id}")
async def battle_room(request: Request, battle_id: int):
    loaded = await load_page(request, f"/api/battles/{battle_id}", not_found="That battle doesn't exist.")
    if isinstance(loaded, Response):
        return loaded
    return templates.TemplateResponse(
        request, "battle_room.html", {"battle": loaded[0], "battle_id": battle_id, "battle_json": json.dumps(loaded[0])}
    )


@app.get("/gyms")
async def gyms(request: Request):
    loaded = await load_page(request, "/api/gyms", "/api/custom-gyms")
    if isinstance(loaded, Response):
        return loaded
    data, custom = loaded
    regions = data.get("regions", [])
    all_gyms = [g for group in regions for g in group["gyms"]]
    earned_count = sum(1 for g in all_gyms if g["earned"])
    return templates.TemplateResponse(
        request, "gyms.html",
        {"regions": regions, "earned_count": earned_count, "total_count": len(all_gyms), "custom": custom or {}},
    )


@app.get("/trades")
async def trades(request: Request):
    loaded = await load_page(request)
    return loaded if isinstance(loaded, Response) else templates.TemplateResponse(request, "trades.html", {})


@app.get("/trades/{trade_id}")
async def trade_room(request: Request, trade_id: int):
    loaded = await load_page(request, f"/api/trades/{trade_id}", not_found="That trade doesn't exist.")
    if isinstance(loaded, Response):
        return loaded
    return templates.TemplateResponse(
        request, "trade_room.html", {"trade": loaded[0], "trade_id": trade_id, "trade_json": json.dumps(loaded[0])}
    )


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


# ---------- JSON proxy endpoints for the pages' JS ----------
# The browser never sees the internal API's address or the session token: it
# calls /api/proxy/<path> on this same-origin app, which forwards to
# /api/<path> with the session. Only the routes listed here are forwarded.
PROXY_ROUTES = {
    "GET": [
        "/inventory", "/characters", "/pokedex", "/team", "/collection/{catch_id}", "/species/{dex_id}",
        "/pokemon-config/{dex_id}", "/battles", "/battles/{battle_id}", "/gyms", "/gyms/{gym_key}",
        "/custom-gyms/{owner_id}", "/league", "/rewards", "/league/{generation}/{member_key}", "/legends/{legend_key}",
        "/collection", "/collection/by-species/{dex_id}", "/trainers", "/trainer/{target_id}/collection",
        "/collection-individuals", "/trainer/{target_id}/collection-individuals",
        "/trainer/{target_id}/collection/by-species/{dex_id}", "/trades", "/trades/{trade_id}",
    ],
    "POST": [
        "/character", "/favorite", "/team", "/store/buy", "/collection/transfer", "/collection/{catch_id}/nickname",
        "/collection/{catch_id}/convert-candy", "/collection/{catch_id}/evolve", "/pokemon-config/{dex_id}",
        "/battles/{battle_id}/cheer", "/pokemon-config/{dex_id}/item", "/pokemon-config/{dex_id}/form",
        "/battles/gym/{gym_key}", "/my-gym", "/battles/custom-gym/{owner_id}", "/rewards/mega",
        "/battles/elite4/{generation}/{member_key}", "/battles/champion/{generation}", "/battles/legend/{legend_key}",
        "/battles/{battle_id}/accept", "/battles/{battle_id}/decline", "/battles/{battle_id}/action",
        "/battles/{battle_id}/forced-switch", "/battles/{battle_id}/forfeit", "/trades/propose",
        "/trades/start/{target_user_id}", "/trades/{trade_id}/accept", "/trades/{trade_id}/decline",
        "/trades/{trade_id}/cancel", "/trades/{trade_id}/offer", "/trades/{trade_id}/confirm",
    ],
}
# Spectators see this when they cheer while logged out.
PROXY_LOGIN_MESSAGES = {"/battles/{battle_id}/cheer": "Log in to cheer"}


def _proxy(method: str, login_message: str):
    async def endpoint(request: Request):
        session = request.cookies.get(SESSION_COOKIE)
        if not session:
            return JSONResponse({"detail": login_message}, status_code=401)
        api_path = "/api/" + request.url.path[len("/api/proxy/"):]
        if method == "GET":
            status, data = await api_get(session, api_path)
        else:
            raw = await request.body()
            status, data = await api_post(session, api_path, json.loads(raw) if raw else {})
        return JSONResponse(data, status_code=status)
    return endpoint


for _method, _paths in PROXY_ROUTES.items():
    for _path in _paths:
        app.add_api_route(f"/api/proxy{_path}", _proxy(_method, PROXY_LOGIN_MESSAGES.get(_path, "Not logged in")),
                          methods=[_method], include_in_schema=False)


# ---------- WebSocket relay ----------
# The browser only ever talks to this same-origin app (auth via its own
# httponly session cookie, exactly like every HTTP route above) — this proxy
# is what actually holds the second, server-to-server WebSocket connection to
# the internal API using that session's token, relaying pushes back down.
# If the reverse proxy in front of either service doesn't yet pass WebSocket
# upgrades through, this connection simply fails to open and the page falls
# back to polling — nothing breaks, it just isn't instant until that's
# configured (see deploy/README.md).

async def relay_websocket(websocket: WebSocket, upstream_path: str):
    session = websocket.cookies.get(SESSION_COOKIE)
    if not session:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    try:
        async with websockets.connect(f"{API_WS_BASE_URL}{upstream_path}?token={session}", open_timeout=10) as upstream:
            async def pump_upstream_to_client():
                async for message in upstream:
                    await websocket.send_text(message)

            async def pump_client_to_upstream():
                # The client never sends real actions over this socket (those
                # go through the POST proxy endpoints above) — this just keeps
                # the receive loop alive so we notice a disconnect.
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
        log.warning(f"WebSocket relay for {upstream_path} closed early: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/battles/{battle_id}")
async def battle_websocket_relay(websocket: WebSocket, battle_id: int):
    await relay_websocket(websocket, f"/ws/battles/{battle_id}")


@app.websocket("/ws/trades/{trade_id}")
async def trade_websocket_relay(websocket: WebSocket, trade_id: int):
    await relay_websocket(websocket, f"/ws/trades/{trade_id}")
