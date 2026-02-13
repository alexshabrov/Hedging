#!/bin/bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <domain>"
    exit 1
fi

DOMAIN="$1"

docker compose run --rm --entrypoint "\
  certbot certonly --webroot \
  -w /var/www/certbot \
  -d ${DOMAIN} \
  --email alexey.shabrov@gmail.com \
  --agree-tos \
  --no-eff-email" certbot

sed "s/__DOMAIN__/${DOMAIN}/g" ./nginx/site-https.template.conf > ./nginx/site.conf

docker compose exec nginx nginx -s reload

echo "Domain ${DOMAIN} is configured. HTTPS config activated."
