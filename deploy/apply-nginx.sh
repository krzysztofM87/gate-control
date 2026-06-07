#!/usr/bin/env bash
set -e

PROJECT_DIR="/opt/gate-control"
NGINX_SOURCE="$PROJECT_DIR/deploy/nginx/gate-control.conf"
NGINX_TARGET="/etc/nginx/sites-available/gate-control"

echo "Applying Nginx config..."

if [ ! -f "$NGINX_SOURCE" ]; then
    echo "ERROR: Nginx config not found: $NGINX_SOURCE"
    exit 1
fi

sudo cp "$NGINX_SOURCE" "$NGINX_TARGET"

sudo ln -sf "$NGINX_TARGET" /etc/nginx/sites-enabled/gate-control
sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t
sudo systemctl reload nginx

echo "Nginx config applied."
