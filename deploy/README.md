# Pokémon Web App — Deployment Guide

Two machines, two processes:

- **Oracle VM** (existing bot machine): runs the Discord bot (`bot.py`) *and* a new
  internal API (`webapi.py`) that reads `pokemon.db` directly. Only the API needs
  new setup here.
- **AWS EC2**: runs the public-facing web app (`web/main.py`). It holds no data of
  its own — every page it renders comes from calling the VM's API.

They talk to each other over HTTPS using a short-lived JWT session, issued when a
user runs `/poke web` in Discord.

## 1. Oracle VM — internal API

```bash
cd /home/ubuntu/shawtybot
git pull
source venv/bin/activate
pip install -r requirements.txt   # now includes fastapi/uvicorn/pyjwt
```

Add to `.env`:

```
WEB_JWT_SECRET=<generate with: python3 -c "import secrets; print(secrets.token_urlsafe(48))">
WEB_BASE_URL=https://poke.yourdomain.com     # the EC2 app's public URL, once it has one
```

Restart the bot so it picks up the new `/poke web` command and DB migration:

```bash
sudo systemctl restart shawtybot
```

Install and start the API service:

```bash
sudo cp deploy/shawtybot-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shawtybot-api
sudo systemctl status shawtybot-api
```

This binds to `127.0.0.1:8000` only — **not** exposed to the internet directly.
Put a reverse proxy (nginx or Caddy) in front of it with a real TLS certificate
(e.g. via Let's Encrypt/certbot), proxying `https://api.yourdomain.com` →
`127.0.0.1:8000`. The web app must reach this over HTTPS, or browsers will block
the requests once the web app itself is served over HTTPS (mixed content).

## 2. AWS EC2 — public web app

Once the machine is clear:

```bash
git clone <your repo url> shawtybot
cd shawtybot
python3 -m venv venv
source venv/bin/activate
pip install -r web/requirements.txt
```

Set the real values in `deploy/shawtybot-web.service` before installing it:

- `API_BASE_URL` → the VM API's public HTTPS URL (e.g. `https://api.yourdomain.com`)
- `COOKIE_SECURE=true` (keep this — it requires the web app itself to be served
  over HTTPS too)

```bash
sudo cp deploy/shawtybot-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shawtybot-web
```

Same as the VM side: put nginx/Caddy in front of `127.0.0.1:8080` with a real
certificate for whatever domain points at this EC2 box, and set that domain as
`WEB_BASE_URL` back on the Oracle VM's `.env` so `/poke web` links to it.

## 3. Try it

In Discord: `/poke web` → click the link → should land on your profile page.

## Notes / current limitations

- The web app is currently **read-only** for team management — editing your team
  still happens via `/poke team` in Discord. Web-based team editing and the
  battling feature are the next phases.
- Sessions last 7 days; the one-time `/poke web` link itself expires in 10 minutes
  and can only be used once.
- Both services must be reachable over HTTPS for cookies/tokens to work reliably
  once real domains are in place — plain HTTP is fine only for local testing.
