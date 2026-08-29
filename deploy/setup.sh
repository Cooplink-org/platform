#!/usr/bin/env bash
# Cooplink backend — one-shot server bootstrap.
# Supports Debian/Ubuntu (apt) and RHEL/AlmaLinux/Rocky (dnf).
#
# Run as root (or with sudo) after the code is at /opt/cooplink:
#   sudo DOMAIN=api.example.com FRONTEND_DOMAIN=example.com bash deploy/setup.sh
#
# WARNING (RHEL/AlmaLinux): if SELinux is Enforcing, this script enables
# httpd_can_network_connect so nginx can proxy to gunicorn.
set -euo pipefail

DOMAIN="${DOMAIN:?set DOMAIN, e.g. api.cooplink.uz}"
FRONTEND_DOMAIN="${FRONTEND_DOMAIN:?set FRONTEND_DOMAIN, e.g. coopl.vercel.app}"
APP_DIR=/opt/cooplink
APP_USER=cooplink
DB_NAME=cooplink
DB_USER=cooplink

# --- Detect package manager ---
if command -v dnf >/dev/null 2>&1; then PKG=dnf;  INIT=systemd_rhel
elif command -v apt-get >/dev/null 2>&1; then PKG=apt; INIT=systemd_deb
else echo "Unsupported package manager"; exit 1; fi

echo "==> Package manager: $PKG"

echo "==> Installing system packages"
if [ "$PKG" = "apt" ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3.12 python3.12-venv python3-pip \
    postgresql postgresql-contrib redis-server nginx certbot python3-certbot-nginx \
    git curl build-essential libpq-dev
else
  dnf install -y epel-release
  dnf install -y python3 python3-pip python3-devel gcc make libpq-devel \
    postgresql-server postgresql redis nginx git curl
  # certbot (EPEL)
  dnf install -y certbot python3-certbot-nginx || \
    dnf install -y certbot
fi

echo "==> Creating app user"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /sbin/nologin "$APP_USER"

echo "==> Setting up PostgreSQL"
systemctl enable --now postgresql
if [ "$PKG" = "dnf" ]; then
  # RHEL: must init the cluster once
  if [ ! -f /var/lib/pgsql/data/PG_VERSION ]; then
    postgresql-setup --initdb
    systemctl enable --now postgresql
  fi
fi
runuser -u postgres -- psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
  runuser -u postgres -- psql -c "CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$(openssl rand -base64 24)'"
runuser -u postgres -- psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
  runuser -u postgres -- psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER"

echo "==> Installing uv + Python deps"
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
cd "$APP_DIR"
uv sync --frozen --no-dev
# The .venv is owned by root here; hand the whole app dir to the app user.
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Writing production .env"
if [ ! -f "$APP_DIR/.env" ]; then
  SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')"
  FERNET_KEY="$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
  DB_PASS="$(openssl rand -base64 24)"
  runuser -u postgres -- psql -c "ALTER ROLE $DB_USER WITH PASSWORD '$DB_PASS'"
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
runuser -u "$APP_USER" -- bash -c "cd $APP_DIR && \
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
if [ "$PKG" = "apt" ]; then
  sed "s/YOUR_DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx/cooplink.conf" > /etc/nginx/sites-available/cooplink
  ln -sf /etc/nginx/sites-available/cooplink /etc/nginx/sites-enabled/cooplink
  rm -f /etc/nginx/sites-enabled/default
else
  sed "s/YOUR_DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx/cooplink.conf" > /etc/nginx/conf.d/cooplink.conf
  rm -f /etc/nginx/conf.d/default.conf
fi
nginx -t
systemctl enable --now nginx

if [ "$PKG" = "dnf" ]; then
  echo "==> SELinux: allow nginx -> gunicorn (httpd_can_network_connect)"
  setsebool -P httpd_can_network_connect on || echo "    (setsebool unavailable — ignore if SELinux disabled)"
fi

certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" --redirect || \
  echo "    certbot failed (DNS for $DOMAIN must point here first). Re-run: certbot --nginx -d $DOMAIN"

echo "==> Done. Check status:"
echo "    systemctl status cooplink-web cooplink-worker cooplink-beat"
echo "    curl -I https://$DOMAIN/api/health/"
