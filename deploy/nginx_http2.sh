#!/usr/bin/env bash
# Turns on HTTP/2 for the HTTPS site proxying to the app on port $1, so a
# browser fetches all of a page's files over one connection at once instead
# of ~6 at a time over HTTP/1.1. (WebSockets keep working: browsers open
# those over a separate HTTP/1.1 connection.) Safe to run on every deploy:
# does nothing once enabled, and restores the old config if nginx -t
# rejects the change. Never fails the deploy.
set -u
PORT="${1:?usage: nginx_http2.sh <app port>}"

command -v nginx >/dev/null 2>&1 || { echo "nginx not installed, skipping"; exit 0; }

# -R (not -r): the files in sites-enabled are usually symlinks, which -r skips.
found=$(sudo grep -lRE "proxy_pass +https?://[^;]*:$PORT" /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | xargs -r -n1 readlink -f | sort -u)
backups=()
for f in $found; do
  # Only "listen ... 443 ssl" lines that don't already say http2.
  sudo grep -E '^[[:space:]]*listen[^;]*443[^;]*ssl' "$f" | grep -qv http2 || continue
  sudo cp "$f" "/var/backups/$(basename "$f").bak-h2"
  backups+=("$f")
  sudo sed -i -E '/http2/! s#^([[:space:]]*listen[^;]*443[^;]*ssl)([^;]*;)#\1 http2\2#' "$f"
done

if [ "${#backups[@]}" = 0 ]; then
  echo "nginx: HTTP/2 already enabled (or no HTTPS site for :$PORT found)"
  exit 0
fi
if sudo nginx -t 2>&1; then
  sudo systemctl reload nginx && echo "nginx: HTTP/2 enabled in ${backups[*]}"
else
  echo "nginx: config test failed, restoring the previous config"
  for f in "${backups[@]}"; do sudo cp "/var/backups/$(basename "$f").bak-h2" "$f"; done
  sudo nginx -t >/dev/null 2>&1 || echo "nginx: WARNING, config still failing"
fi
exit 0
