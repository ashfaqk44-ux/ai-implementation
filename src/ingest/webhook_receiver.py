"""C3 Webhook receiver (FastAPI).

Pixxi posts the raw object (lead, note, reminder, listing) to the callback URL registered
for each event; the body does not say which event it is. So each event gets its own
route, /webhooks/pixxi/<event-slug>, and scripts/subscribe_webhooks.py registers them.

Pixxi documents no webhook signature, so callback URLs carry a shared secret (?key=...).

For every delivery the receiver decides accept or ignore, logs the decision and reason in
the middleware store, and passes accepted events to a handler (the orchestrator, from
build step 4). It ignores:
- duplicates (same event, object id and timestamp);
- notes and reminders carrying the AI tag (the system's own writes);
- UPDATE_LEADS on a lead the system wrote to within SELF_WRITE_WINDOW_SECONDS, because
  Pixxi may fire it as a side effect of our note or reminder (worst-case assumption until
  verified; docs/api_findings.md checks 1 and 2).

Run: uvicorn src.ingest.webhook_receiver:create_app --factory --port 8002
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.audit.note import is_ai_message
from src.audit.store import MiddlewareStore
from src.config import Settings, get_settings

log = logging.getLogger("pixxi.webhooks")

# URL slug -> Pixxi event name (POST /pixxiapi/webhook/v1/subscribe enum)
EVENT_SLUGS: dict[str, str] = {
    "add-leads": "ADD_LEADS",
    "update-leads": "UPDATE_LEADS",
    "add-lead-note": "ADD_LEAD_NOTE",
    "add-reminder": "ADD_REMINDER",
    "add-listings": "ADD_LISTINGS",
    "update-listings": "UPDATE_LISTINGS",
    "delete-listings": "DELETE_LISTINGS",
}
SLUG_FOR_EVENT = {v: k for k, v in EVENT_SLUGS.items()}


class WebhookEvent(BaseModel):
    event: str
    payload: dict[str, Any]
    lead_id: int | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Decision(BaseModel):
    status: str  # accepted | ignored
    reason: str


def callback_path(event: str) -> str:
    return f"/webhooks/pixxi/{SLUG_FOR_EVENT[event]}"


def _lead_id(event: str, p: dict) -> int | None:
    value: Any = None
    if event in ("ADD_LEADS", "UPDATE_LEADS"):
        value = p.get("id")
    elif event == "ADD_LEAD_NOTE":
        value = p.get("leadId")
    elif event == "ADD_REMINDER" and p.get("object_type") == "LEADS_REMINDER":
        value = p.get("clientId") or p.get("object_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dedup_key(event: str, p: dict) -> str:
    stamp = p.get("updateTime") or p.get("update_time") or p.get("createTime") or p.get("create_time") or ""
    return f"{event}:{p.get('id')}:{stamp}"


def decide(event: str, payload: dict, store: MiddlewareStore, settings: Settings) -> Decision:
    """Pure decision logic, separated from HTTP so it can be tested and explained."""
    if event == "ADD_LEAD_NOTE" and is_ai_message(payload.get("message")):
        return Decision(status="ignored", reason="self-write: note carries the AI tag")
    if event == "ADD_REMINDER" and (
        is_ai_message(payload.get("title")) or is_ai_message(payload.get("description"))
    ):
        return Decision(status="ignored", reason="self-write: reminder carries the AI tag")
    lead_id = _lead_id(event, payload)
    if event == "UPDATE_LEADS" and lead_id is not None and store.wrote_recently(
        lead_id, settings.self_write_window_seconds
    ):
        return Decision(
            status="ignored",
            reason=f"self-write: system wrote to lead {lead_id} within "
            f"{settings.self_write_window_seconds}s",
        )
    if not store.mark_seen(_dedup_key(event, payload)):
        return Decision(status="ignored", reason="duplicate delivery")
    return Decision(status="accepted", reason="new event")


def create_app(
    settings: Settings | None = None,
    store: MiddlewareStore | None = None,
    handler: Callable[[WebhookEvent], None] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    store = store or MiddlewareStore(settings.state_db)
    app = FastAPI(title="Pixxi Agents webhook receiver (C3)")
    app.state.store = store
    app.state.accepted: list[WebhookEvent] = []
    app.state.handler = handler or app.state.accepted.append

    if settings.webhook_secret is None:
        log.warning("WEBHOOK_SECRET is not set: callback URLs are unauthenticated")

    def _check_key(key: str | None) -> None:
        if settings.webhook_secret is None:
            return
        expected = settings.webhook_secret.get_secret_value()
        if key is None or not hmac.compare_digest(key, expected):
            raise HTTPException(status_code=403, detail="invalid webhook key")

    @app.post("/webhooks/pixxi/{slug}")
    async def receive(slug: str, request: Request, background: BackgroundTasks,
                      key: str | None = Query(default=None)) -> dict:
        _check_key(key)
        event = EVENT_SLUGS.get(slug)
        if event is None:
            raise HTTPException(status_code=404, detail="unknown event")
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")

        decision = decide(event, payload, store, settings)
        store.log_webhook(event, str(payload.get("id")), decision.status, decision.reason, payload)
        log.info("%s id=%s -> %s (%s)", event, payload.get("id"), decision.status, decision.reason)
        if decision.status == "accepted":
            background.add_task(
                app.state.handler,
                WebhookEvent(event=event, payload=payload, lead_id=_lead_id(event, payload)),
            )
        # Always 200 for valid deliveries so Pixxi does not retry ignored events.
        return decision.model_dump()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
