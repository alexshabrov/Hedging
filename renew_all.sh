#!/bin/bash
set -euo pipefail

echo "Starting certificate renewal process..."

docker compose run --rm --entrypoint "\
  certbot renew \
  --webroot -w /var/www/certbot \
  --email alexey.shabrov@gmail.com \
  --agree-tos \
  --no-eff-email" certbot

docker compose exec nginx nginx -s reload

echo "Certificate renewal process finished."
