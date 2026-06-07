# Deploy

## Server

Backend FastAPI runs in Docker on the VPS.

Local container port mapping:

    127.0.0.1:8010 -> 8000

Main server directory on VPS:

    /opt/gate-control/server

Run or rebuild backend:

    cd /opt/gate-control/server
    docker compose up -d --build

Check backend locally on VPS:

    curl http://127.0.0.1:8010
    curl http://127.0.0.1:8010/health

Check container logs:

    docker logs -f gate-server

## Nginx

Nginx config is stored in:

    deploy/nginx/gate-control.conf

Target path on VPS:

    /etc/nginx/sites-available/gate-control

Apply config manually:

    sudo cp /opt/gate-control/deploy/nginx/gate-control.conf /etc/nginx/sites-available/gate-control
    sudo ln -sf /etc/nginx/sites-available/gate-control /etc/nginx/sites-enabled/gate-control
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo nginx -t
    sudo systemctl reload nginx

Or use helper script:

    cd /opt/gate-control
    bash deploy/apply-nginx.sh

Public URL:

    http://tools.malmaz.com

Backend proxy target:

    http://127.0.0.1:8010

## HTTPS

After DNS points to the VPS, enable HTTPS:

    sudo apt install -y certbot python3-certbot-nginx
    sudo certbot --nginx -d tools.malmaz.com

Test renewal:

    sudo certbot renew --dry-run
