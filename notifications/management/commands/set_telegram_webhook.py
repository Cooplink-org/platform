"""
Management command to register / inspect the Telegram webhook.

Usage:
    python manage.py set_telegram_webhook          # register webhook
    python manage.py set_telegram_webhook --check   # show current webhook status
    python manage.py set_telegram_webhook --delete  # unregister webhook
    python manage.py set_telegram_webhook --dry-run # print what would be done

The webhook URL is built from FRONTEND_URL (or TELEGRAM_WEBHOOK_BASE_URL if set).
Telegram requires a publicly accessible HTTPS URL (except localhost for testing).

Run this on every deploy after FRONTEND_URL changes.
"""

import os

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Register / inspect / delete the Telegram bot webhook"

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete the webhook instead of setting it",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Show current webhook registration status from Telegram",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the URL that would be registered without calling Telegram",
        )

    def handle(self, *_args, **options):
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not bot_token:
            raise CommandError("TELEGRAM_BOT_TOKEN is not set in environment")

        # --check: show current webhook status
        if options["check"]:
            self._show_webhook_info(bot_token)
            return

        # --delete: unregister webhook
        if options["delete"]:
            if options["dry_run"]:
                self.stdout.write("Would call deleteWebhook")
                return
            self._delete_webhook(bot_token)
            return

        webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        if not webhook_secret:
            raise CommandError("TELEGRAM_WEBHOOK_SECRET is not set in environment")

        # Build the webhook URL
        # TELEGRAM_WEBHOOK_BASE_URL overrides FRONTEND_URL so the webhook can
        # point to a different domain than the frontend (e.g. the Django API).
        base_url = os.environ.get(
            "TELEGRAM_WEBHOOK_BASE_URL",
            settings.FRONTEND_URL,
        )
        # Strip trailing slash for consistency
        base_url = base_url.rstrip("/")
        webhook_url = f"{base_url}/api/telegram/webhook/{webhook_secret}/"

        if options["dry_run"]:
            self.stdout.write(f"Would register webhook: {webhook_url}")
            return

        self._set_webhook(bot_token, webhook_url)

    def _api_call(self, bot_token: str, method: str, payload: dict | None = None):
        """Make a call to the Telegram Bot API and return parsed JSON."""
        url = f"https://api.telegram.org/bot{bot_token}/{method}"
        try:
            resp = requests.post(url, json=payload or {}, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise CommandError(f"Telegram API call failed ({method}): {exc}") from exc

    def _show_webhook_info(self, bot_token: str):
        """Display current webhook registration status."""
        result = self._api_call(bot_token, "getWebhookInfo")
        info = result.get("result", {})

        self.stdout.write("--- Webhook Status ---")
        url = info.get("url", "")
        self.stdout.write(f"URL      : {url or '(not set)'}")
        self.stdout.write(f"Pending  : {info.get('pending_update_count', 0)} updates")
        last_error = info.get("last_error_message", "")
        if last_error:
            self.stderr.write(self.style.ERROR(f"Last err : {last_error}"))
        last_error_date = info.get("last_error_date")
        if last_error_date:
            from datetime import datetime

            dt = datetime.fromtimestamp(last_error_date)
            self.stdout.write(f"Err at   : {dt.isoformat()}")
        allowed = info.get("allowed_updates")
        if allowed:
            self.stdout.write(f"Updates  : {', '.join(allowed)}")
        max_conn = info.get("max_connections")
        if max_conn:
            self.stdout.write(f"Max conn : {max_conn}")

        if not url:
            self.stdout.write(
                self.style.WARNING("\nWebhook is NOT registered. Run without --check to register.")
            )
        elif last_error:
            self.stdout.write(
                self.style.WARNING(
                    "\nWebhook is registered but Telegram reports errors reaching it. "
                    "Check that the URL is publicly accessible and uses HTTPS."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("\nWebhook is registered and reachable."))

    def _set_webhook(self, bot_token: str, webhook_url: str):
        """Register webhook with Telegram."""
        payload = {
            "url": webhook_url,
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        }

        bot_api_secret = os.environ.get("TELEGRAM_BOT_API_SECRET_TOKEN", "")
        if bot_api_secret:
            payload["secret_token"] = bot_api_secret

        result = self._api_call(bot_token, "setWebhook", payload)
        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS(f"Webhook registered: {webhook_url}"))
        else:
            raise CommandError(f"Telegram API error: {result}")

    def _delete_webhook(self, bot_token: str):
        """Unregister webhook."""
        result = self._api_call(bot_token, "deleteWebhook", {"drop_pending_updates": True})
        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS("Webhook deleted"))
        else:
            raise CommandError(f"Telegram API error: {result}")
