"""
Public-facing web app — hosted separately from the bot (e.g. on EC2).
Holds no game data itself; every page fetches from the internal API
(webapi.py, running on the bot's machine) using the session token
issued at /login, forwarded as a cookie between browser and this app.

Run with: uvicorn web.main:app --host 0.0.0.0 --port 8080

CD test marker: deploy-web.yml pipeline
"""
import os
import json
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

log = logging.getLogger("web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
SESSION_COOKIE = "session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60  # 7 days, matches the API's JWT expiry

BASE_DIR = os.path.dirname(__file__)

app = FastAPI(title="ShawtyBot Pokémon Web")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


async def api_get(session: str, path: str) -> tuple[int, dict | list]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_BASE_URL}{path}", headers={"Authorization": f"Bearer {session}"})
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {}


async def api_post(session: str, path: str, payload: dict) -> tuple[int, dict | list]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
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
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{API_BASE_URL}/api/auth/exchange", json={"token": token})

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

    status, data = await api_get(session, "/api/me")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    _, achievements = await api_get(session, "/api/achievements")

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


@app.get("/trainer/{target_id}")
async def view_trainer(request: Request, target_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, f"/api/trainer/{target_id}")
    if status == 401:
        return clear_session(RedirectResponse("/"))
    if status == 404:
        return templates.TemplateResponse(request, "landing.html", {"error": "That trainer doesn't exist."})

    _, achievements = await api_get(session, f"/api/trainer/{target_id}/achievements")

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

    status, data = await api_get(session, "/api/collection")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "collection.html", {"collection": data})


# ---------- JSON proxy endpoints for the customize modal's JS ----------
# These exist so client-side JS never sees the internal API's session token
# or hostname directly — it only ever talks to this same-origin app.

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


@app.post("/api/proxy/collection/{catch_id}/nickname")
async def proxy_set_nickname(request: Request, catch_id: int):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    body = await request.json()
    status, data = await api_post(session, f"/api/collection/{catch_id}/nickname", body)
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
