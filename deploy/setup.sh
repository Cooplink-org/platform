#!/usr/bin/env bash
# Cooplink backend — one-shot server bootstrap (Ubuntu 22.04/24.04, no Docker).
#
# Usage:
#   DOMAIN=api.cooplink.uz FRONTEND_DOMAIN=cooplink.uz sudo -E bash setup.sh
#
# Preconditions:
#   - Code already lives at /opt/cooplink (upload it / git clone there)
#   - Run as root
set -euo pipefail

DOMAIN="${DOMAIN:?set DOMAIN, e.g. api.cooplink.uz}"
FRONTEND_DOMAIN="${FRONTEND_DOMAIN:?set FRONTEND_DOMAIN, e.g. cooplink.uz}"
APP_DIR=/opt/cooplink
APP_USER=cooplink
DB_NAME=cooplink
DB_USER=cooplink

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3.12 python3.12-venv python3-pip \
    postgresql postgresql-contrib redis-server nginx certbot python3-certbot-nginx \
    git curl build-essential libpq-dev

echo "==> Creating app user"
id -u "$APP_USER" &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

echo "==> Setting up PostgreSQL"
# Start PG and create role + db if missing
systemctl enable --now postgresql
su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'\" | grep -q 1 || psql -c \"CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$(openssl rand -base64 24)';\""
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='$DB_NAME'\" | grep -q 1 || psql -c \"CREATE DATABASE $DB_NAME OWNER $DB_USER;\""

echo "==> Installing uv + Python deps"
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
cd "$APP_DIR"
sudo -u "$APP_USER" bash -c 'export PATH="$HOME/.local/bin:$PATH"; uv sync --frozen --no-dev'

echo "==> Writing production .env"
if [ ! -f "$APP_DIR/.env" ]; then
  SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')"
  FERNET_KEY="$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
  DB_PASS="$(openssl rand -base64 24)"
  su - postgres -c "psql -c \"ALTER ROLE $DB_USER WITH PASSWORD '$DB_PASS';\""
  sed -e "s/__CHANGE_ME_SECRET_KEY__/$SECRET_KEY/" \
      -e "s/__CHANGE_ME_FERNET__/$FERNET_KEY/" \
      -e "s/__DB_PASSWORD__/$DB_PASS/" \
      -e "s/YOUR_DOMAIN/$DOMAIN/g" \
      -e "s/YOUR_FRONTEND_DOMAIN/$FRONTEND_DOMAIN/g" \
      "$APP_DIR/deploy/env.prod.template" > "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "    .env created. Fill in GITHUB_*, payment and Telegram secrets in $APP_DIR/.env"
else
  echo "    $APP_DIR/.env already exists — leaving it untouched"
fi

echo "==> Django migrate + collectstatic"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && export PATH=\"\$HOME/.local/bin:\$PATH\" && \
  DJANGO_ENV=prod .venv/bin/python manage.py migrate --no-input && \
  DJANGO_ENV=prod .venv/bin/python manage.py collectstatic --no-input"

echo "==> Installing systemd units"
for s in web worker beat; do
  cp "$APP_DIR/deploy/systemd/cooplink-$s.service" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now cooplink-web cooplink-worker cooplink-beat

echo "==> Installing nginx site + Let's Encrypt"
mkdir -p /var/www/letsencrypt
sed "s/YOUR_DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx/cooplink.conf" > /etc/nginx/sites-available/cooplink
ln -sf /etc/nginx/sites-available/cooplink /etc/nginx/sites-enabled/cooplink
# Remove default site if present
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" --redirect || \
  echo "    certbot failed (DNS for $DOMAIN must point here first). Re-run: certbot --nginx -d $DOMAIN"

echo "==> Done. Check status:"
echo "    systemctl status cooplink-web cooplink-worker cooplink-beat"
echo "    curl -I https://$DOMAIN/api/health/"
