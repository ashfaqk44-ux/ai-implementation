"""Register the webhook receiver's callback URLs with Pixxi (or the mock).

Pixxi webhook bodies carry no event name, so every event gets its own callback URL:
    <WEBHOOK_PUBLIC_BASE_URL>/webhooks/pixxi/<event-slug>?key=<WEBHOOK_SECRET>

Usage:
    .venv\\Scripts\\python -m scripts.subscribe_webhooks            # subscribe all
    .venv\\Scripts\\python -m scripts.subscribe_webhooks --list     # show subscriptions
    .venv\\Scripts\\python -m scripts.subscribe_webhooks --unsubscribe

With the real API, WEBHOOK_PUBLIC_BASE_URL must be reachable from the internet
(e.g. through a tunnel), because Pixxi calls it from its servers.
"""

from __future__ import annotations

import argparse
from urllib.parse import quote

from src.config import get_settings
from src.ingest.webhook_receiver import EVENT_SLUGS, callback_path
from src.pixxi.http import PixxiHttp


def callback_url(event: str) -> str:
    s = get_settings()
    url = s.webhook_public_base_url.rstrip("/") + callback_path(event)
    if s.webhook_secret is not None:
        url += "?key=" + quote(s.webhook_secret.get_secret_value())
    return url


def _redact(url: str) -> str:
    return url.split("?key=")[0] + ("?key=***" if "?key=" in url else "")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true")
    group.add_argument("--unsubscribe", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    http = PixxiHttp(settings)
    print(f"Pixxi: {settings.pixxi_base_url} (mode={settings.pixxi_mode})")
    try:
        if args.list:
            for sub in http.request("GET", "/pixxiapi/webhook/v1/subscription/list", kind="read") or []:
                print(f"  {sub.get('event'):<16} {sub.get('status'):<8} {_redact(sub.get('callbackUrl', ''))}")
            return
        for event in EVENT_SLUGS.values():
            url = callback_url(event)
            if args.unsubscribe:
                http.request("POST", "/pixxiapi/webhook/v1/unsubscribe", kind="write",
                             json={"event": event, "url": url})
                print(f"  unsubscribed {event}")
            else:
                http.request("POST", "/pixxiapi/webhook/v1/subscribe", kind="write",
                             json={"event": event, "callbackUrl": url})
                print(f"  subscribed   {event:<16} -> {_redact(url)}")
    finally:
        http.close()


if __name__ == "__main__":
    main()
