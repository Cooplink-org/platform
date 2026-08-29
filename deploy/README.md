# Cooplink Backend — Server Deployment (Ubuntu, no Docker)

This folder contains everything needed to run the Cooplink Django backend on a
bare Linux server (tested on Ubuntu 22.04 / 24.04) without Docker:

- `setup.sh` — one-shot bootstrap (installs Postgres, Redis, nginx, certbot, deps, SSL, systemd)
- `systemd/cooplink-{web,worker,beat}.service` — gunicorn + 2 Celery processes
- `nginx/cooplink.conf` — HTTPS reverse proxy (certbot-managed)
- `env.prod.template` — production env template → becomes `/opt/cooplink/.env`

Architecture: `nginx` (TLS) → `gunicorn` (127.0.0.1:8000) → Django.
Celery `worker` + `beat` run as separate systemd units, backed by local Redis.
PostgreSQL runs locally as the primary database.

---

## Prerequisites

- A Linux server with a public IPv4. Tested on **Ubuntu 22.04/24.04** and
  **AlmaLinux / Rocky / RHEL 9** (the script auto-detects `apt` vs `dnf`).
- Run the script as **root** (or `sudo`). On RHEL-family boxes, SELinux is
  handled automatically by the script (`httpd_can_network_connect`).
- A domain (or subdomain) with an **A record** pointing at the server IP
  (e.g. `api.cooplink.uz → 1.2.3.4`). SSL will fail if DNS doesn't resolve yet.
- Code placed at `/opt/cooplink` (either `git clone` or upload the folder).
- Run everything as **root**.

```bash
# Get the code onto the server
git clone https://github.com/Cooplink-org/platform.git /opt/cooplink
# or rsync -avz ./ user@server:/opt/cooplink
```

---

## Deploy

```bash
cd /opt/cooplink
DOMAIN=api.cooplink.uz FRONTEND_DOMAIN=cooplink.uz sudo -E bash deploy/setup.sh
```

What the script does, in order:

1. Installs `python3.12`, `postgresql`, `redis-server`, `nginx`, `certbot`, `uv`.
2. Creates a system user `cooplink` to run the app.
3. Creates the Postgres role + database `cooplink`.
4. Runs `uv sync --frozen --no-dev` to build the `.venv`.
5. Generates `SECRET_KEY`, `FERNET_KEY`, and the DB password, then writes
   `/opt/cooplink/.env` from `env.prod.template`.
6. Runs `migrate` + `collectstatic`.
7. Installs & starts the three systemd units.
8. Installs the nginx site and requests a Let's Encrypt certificate.

### After the script

Open `/opt/cooplink/.env` and fill in the placeholders the script cannot
generate for you:

- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` (GitHub OAuth App)
- `MIRPAY_KASSA_ID` / `MIRPAY_API_KEY` (optional)
- `INPAY_MERCHANT_ID` / `INPAY_MERCHANT_TOKEN` (optional)
- `TELEGRAM_*` (optional)

Then restart so the new env is picked up:

```bash
systemctl restart cooplink-web cooplink-worker cooplink-beat
```

### Verify

```bash
systemctl status cooplink-web cooplink-worker cooplink-beat
curl -I https://api.cooplink.uz/api/health/      # expect HTTP/2 200
```

---

## Future updates (new code)

```bash
cd /opt/cooplink
git pull

# Re-sync deps if pyproject/uv.lock changed
sudo -u cooplink bash -c 'export PATH="$HOME/.local/bin:$PATH"; uv sync --frozen --no-dev'

# Apply migrations + static
sudo -u cooplink bash -c 'export PATH="$HOME/.local/bin:$PATH"; \
  DJANGO_ENV=prod .venv/bin/python manage.py migrate --no-input && \
  DJANGO_ENV=prod .venv/bin/python manage.py collectstatic --no-input'

# Restart services
systemctl restart cooplink-web cooplink-worker cooplink-beat
```

---

## Logs

```bash
journalctl -u cooplink-web -f      # gunicorn / Django requests
journalctl -u cooplink-worker -f   # celery worker
journalctl -u cooplink-beat -f     # celery beat scheduler
journalctl -u nginx -f             # proxy / TLS errors
```

---

## Troubleshooting

### 502 Bad Gateway from nginx
nginx can't reach gunicorn on `127.0.0.1:8000`.
```bash
systemctl status cooplink-web      # is it running?
ss -ltnp | grep 8000               # is something listening?
journalctl -u cooplink-web -n 50   # crash on boot? usually a missing env var
```
Common cause: `.env` missing or `SECRET_KEY` empty → Django refuses to start.
Fix the `.env`, then `systemctl restart cooplink-web`.

On **AlmaLinux/RHEL with SELinux Enforcing**, a 502 can come from SELinux
blocking nginx→gunicorn even though both are up:
```bash
getsebool httpd_can_network_connect          # expect: on
setsebool -P httpd_can_network_connect on
systemctl reload nginx
```

### RHEL/AlmaLinux: PostgreSQL fails to start
The cluster must be initialised once. The script does this, but if you ran it
manually:
```bash
postgresql-setup --initdb
systemctl enable --now postgresql
```
Also ensure `pg_hba.conf` (`/var/lib/pgsql/data/pg_hba.conf`) allows
`host ... 127.0.0.1/32 scram-sha-256` for the app's TCP connection.

### 400 Bad Request / "DisallowedHost"
`ALLOWED_HOSTS` in `.env` doesn't include the domain.
```bash
# in /opt/cooplink/.env
ALLOWED_HOSTS=api.cooplink.uz
systemctl restart cooplink-web
```

### Site loads over HTTP but not HTTPS / certbot failed
DNS wasn't resolving when the script ran, so Let's Encrypt couldn't validate.
Confirm the A record, then:
```bash
certbot --nginx -d api.cooplink.uz
nginx -t && systemctl reload nginx
```

### Certbot says "too many certificates" / rate limited
Use staging first to test, then the real one:
```bash
certbot --nginx -d api.cooplink.uz --staging
# when confirmed working, delete the staging cert and re-run without --staging
```

### Migrations fail / DB connection refused
```bash
systemctl status postgresql
sudo -u postgres psql -c '\l'            # is the cooplink db present?
# Check the DATABASE_URL in .env matches user/db/password
```

### Celery tasks not running (webhooks, scheduled jobs)
```bash
systemctl status cooplink-worker cooplink-beat
# Redis reachable?
redis-cli ping                          # expect PONG
# Check CELERY_BROKER_URL in .env
```

### Static files 404
`collectstatic` wasn't run after a deploy, or whitenoise isn't serving.
```bash
sudo -u cooplink bash -c 'export PATH="$HOME/.local/bin:$PATH"; \
  DJANGO_ENV=prod .venv/bin/python manage.py collectstatic --no-input'
systemctl restart cooplink-web
```

### Media uploads 403 / not visible
nginx serves `/opt/cooplink/media` directly and needs read access.
```bash
chmod -R a+rX /opt/cooplink/media
chown -R cooplink:cooplink /opt/cooplink/media
```

### Permissions / "address already in use" on port 80/443
Another web server (often the default nginx site) is bound. Remove it:
```bash
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

### Whole app down after a deploy
Roll back and restart:
```bash
git log --oneline -5                 # find last good commit
git checkout <good-sha>
sudo -u cooplink bash -c 'export PATH="$HOME/.local/bin:$PATH"; uv sync --frozen --no-dev'
systemctl restart cooplink-web cooplink-worker cooplink-beat
```

### Renewing the TLS certificate (automatic, but verify)
```bash
certbot renew --dry-run              # should report "simulated success"
# certbot installs a systemd timer; check:
systemctl list-timers | grep certbot
```

### Database backups
```bash
# Manual dump
sudo -u postgres pg_dump cooplink > /var/backups/cooplink-$(date +%F).sql
# Restore
sudo -u postgres psql cooplink < /var/backups/cooplink-YYYY-MM-DD.sql
```
Schedule with a nightly cron job for automated backups.
