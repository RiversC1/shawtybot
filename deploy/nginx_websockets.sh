#!/usr/bin/env bash
# Makes nginx pass WebSocket upgrades through to the app on port $1 (the
# battle/trade rooms' live updates). Without the Upgrade/Connection headers
# nginx forwards /ws/... as plain HTTP, the app answers 404, and every room
# falls back to polling every 2s. Safe to run on every deploy: does nothing
# once the headers are in place, and restores the old config if nginx -t
# rejects the change. Never fails the deploy.
set -u
PORT="${1:?usage: nginx_websockets.sh <app port>}"
MAP_FILE=/etc/nginx/conf.d/websocket_upgrade.conf

command -v nginx >/dev/null 2>&1 || { echo "nginx not installed, skipping"; exit 0; }

changed=0
created_map=0
backups=()
if ! sudo grep -rqs 'connection_upgrade' /etc/nginx/nginx.conf /etc/nginx/conf.d /etc/nginx/sites-enabled; then
  printf 'map $http_upgrade $connection_upgrade {\n    default upgrade;\n    %s      close;\n}\n' "''" | sudo tee "$MAP_FILE" >/dev/null
  changed=1
  created_map=1
fi

for link in $(sudo grep -lrE "proxy_pass +http://(127\.0\.0\.1|localhost):$PORT" /etc/nginx/sites-enabled/ 2>/dev/null); do
  f=$(readlink -f "$link")
  sudo grep -q 'proxy_set_header Upgrade' "$f" && continue
  sudo cp "$f" "/var/backups/$(basename "$f").bak-ws"
  backups+=("$f")
  extra='\1proxy_set_header Upgrade $http_upgrade;\n\1proxy_set_header Connection $connection_upgrade;'
  sudo grep -q 'proxy_http_version' "$f" || extra='\1proxy_http_version 1.1;\n'"$extra"
  sudo sed -i -E "s#^([[:space:]]*)(proxy_pass +http://(127\.0\.0\.1|localhost):$PORT[^;]*;)#\1\2\n$extra#" "$f"
  changed=1
done

if [ "$changed" = 0 ]; then
  echo "nginx: WebSocket upgrades already enabled"
  exit 0
fi
if sudo nginx -t 2>&1; then
  sudo systemctl reload nginx && echo "nginx: WebSocket upgrades enabled"
else
  echo "nginx: config test failed, restoring the previous config"
  for f in "${backups[@]}"; do sudo cp "/var/backups/$(basename "$f").bak-ws" "$f"; done
  [ "$created_map" = 1 ] && sudo rm -f "$MAP_FILE"
  sudo nginx -t >/dev/null 2>&1 || echo "nginx: WARNING, config still failing"
fi
exit 0
