# Pokémon Web App — Deployment Guide

Two machines, two processes:

- **Oracle VM** (existing bot machine): runs the Discord bot (`bot.py`) *and* the
  internal API (`webapi.py`) that reads `pokemon.db` directly.
- **AWS EC2** (provisioned via `terraform/`): runs the public-facing web app
  (`web/main.py`). It holds no data of its own — every page it renders comes from
  calling the VM's API over HTTPS.

They talk to each other over HTTPS using a short-lived JWT session, issued when a
user runs `/poke web` in Discord.

**Live setup (for reference):**
- Web app: `https://shawtypoke-web.duckdns.org` (EC2, currently `34.230.121.211`)
- Internal API: `https://shawtypoke-api.duckdns.org` (Oracle VM, currently `129.80.183.44`)
- Both domains are free [DuckDNS](https://www.duckdns.org) subdomains pointed at
  each box's static IP, with free Let's Encrypt certs via certbot.

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
WEB_BASE_URL=https://shawtypoke-web.duckdns.org
WEB_FRONTEND_ORIGINS=https://shawtypoke-web.duckdns.org
```

Restart the bot so it picks up the `/poke web` command and DB migration:

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

This binds to `127.0.0.1:8000` only. Put nginx in front of it:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

Create `/etc/nginx/sites-available/shawtybot-api`. The `map` block and the
`Upgrade`/`Connection` headers are required for the battle spectate/play
WebSocket (`/ws/battles/{id}`) to work — without them nginx never passes the
`Upgrade` handshake through, and the WebSocket connection just fails to open
(harmlessly — the web battle room automatically falls back to polling every
2s until this is in place, so nothing is broken in the meantime, it just
isn't instant):

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    server_name shawtypoke-api.duckdns.org;   # your own DuckDNS name here

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

(The `map` directive must sit at the `http` context level — either paste it
above the `server` block in this same file, since nginx includes every file
under `sites-enabled/` inside its `http` block, or put it once in
`/etc/nginx/nginx.conf` if you'd rather not repeat it per site.)

```bash
sudo ln -sf /etc/nginx/sites-available/shawtybot-api /etc/nginx/sites-enabled/shawtybot-api
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
sudo certbot --nginx -d shawtypoke-api.duckdns.org --agree-tos --register-unsafely-without-email --redirect
```

### ⚠️ Oracle Cloud gotcha: two separate firewalls

Getting port 80/443 reachable on an OCI instance requires opening it in **two
different places**, both required:

1. **OCI Security List** (cloud-level): Console → Networking → Virtual Cloud
   Networks → your VCN → Subnet → Security List → Add Ingress Rules for TCP
   80 and 443, source `0.0.0.0/0`, source port range left blank/"All".
2. **The VM's own iptables** (OS-level) — Oracle's stock Ubuntu image ships with
   an iptables policy that only explicitly allows **new** connections on port 22
   and rejects everything else, *regardless* of what the Security List allows:

   ```bash
   sudo iptables -L INPUT -n --line-numbers   # find the line number of the REJECT rule
   sudo iptables -I INPUT <n> -p tcp -m state --state NEW -m tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT <n+1> -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save   # persist across reboots (iptables-persistent is preinstalled)
   ```

   `ufw status` showing "inactive" does **not** mean this is a non-issue — these
   rules live directly in iptables, outside of ufw's management.

If `curl https://your-domain/health` hangs from outside but works via `curl
localhost/health` on the box itself, this iptables rule is almost always why.

## 2. AWS EC2 — public web app

Provision the instance first (see `terraform/` — `terraform init && terraform
plan && terraform apply`, then `terraform output -raw private_key_pem` for the
SSH key). AWS's own security group (managed by Terraform, ports 22/80/443) is
enough on this side — no extra OS-level firewall gotcha like the OCI box.

```bash
git clone <your repo url> shawtybot
cd shawtybot
python3 -m venv venv
source venv/bin/activate
pip install -r web/requirements.txt
```

Install nginx + certbot the same way as the VM side, but proxying to
`127.0.0.1:8080` and using your web app's own DuckDNS name — **include the
same `map $http_upgrade $connection_upgrade` block and
`proxy_http_version 1.1` / `Upgrade` / `Connection` headers shown above**.
This box needs it too: the browser's WebSocket connects here first (same
origin, cookie-authenticated), and this app is what opens the second,
server-to-server WebSocket to the internal API. Both hops need WebSocket
upgrades passed through, or the whole chain falls back to polling.

Set the real values in `deploy/shawtybot-web.service` before installing it:

- `API_BASE_URL` → the VM API's public HTTPS URL (e.g. `https://shawtypoke-api.duckdns.org`)
- `COOKIE_SECURE=true` (requires the web app itself to be served over HTTPS too)

```bash
sudo cp deploy/shawtybot-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shawtybot-web
```

## 3. Try it

In Discord: `/poke web` → click the link → should land on your profile page.

## Notes / current limitations

- The web app is currently **read-only** for team management — editing your team
  still happens via `/poke team` in Discord.
- Battling is fully web-based: `/poke challenge`, `/poke gym`, and `/poke trainer`
  in Discord only create the battle and post a link — accepting/declining,
  choosing moves, switching, and forfeiting all happen on `/battles/{id}`.
  Real-time updates use a WebSocket relayed through both nginx configs (see
  above); until that's configured on a given deployment, the page falls back
  to polling every ~2s automatically, so nothing is broken either way.
- Sessions last 7 days; the one-time `/poke web` link itself expires in 10 minutes
  and can only be used once.
- Both services must stay reachable over HTTPS for cookies/tokens to work
  reliably — plain HTTP only ever worked as an interim step before certs existed.
- Certbot's systemd timer (`certbot.timer`) auto-renews both certs; no manual
  action needed unless it fails (`sudo certbot renew --dry-run` to test).
- If either box's public IP ever changes (e.g. EC2 without an Elastic IP,
  or the VM is recreated), update the corresponding DuckDNS record's IP at
  duckdns.org — everything else (nginx, certbot, env vars) references the
  domain name, not the raw IP.
