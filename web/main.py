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

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

log = logging.getLogger("web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
API_WS_BASE_URL = API_BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
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


@app.get("/battles")
async def battles(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/battles")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "battles.html", {"battles": data})


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

    return templates.TemplateResponse(request, "gyms.html", {"gyms": data})


@app.get("/trades")
async def trades(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        return RedirectResponse("/")

    status, data = await api_get(session, "/api/trades")
    if status == 401:
        return clear_session(RedirectResponse("/"))

    return templates.TemplateResponse(request, "trades.html", {"trades": data})


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
