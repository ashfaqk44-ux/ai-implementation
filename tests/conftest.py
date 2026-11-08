"""Shared fixtures: mock Pixxi and the webhook receiver wired together in-process.

When the mock fires a webhook, the dispatcher below delivers it straight to the receiver's
TestClient, so the full loop (write -> Pixxi event -> receiver decision) runs without network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from mock_pixxi.app import create_app as create_mock
from src.audit.store import MiddlewareStore
from src.config import Settings
from src.ingest.webhook_receiver import EVENT_SLUGS, callback_path, create_app as create_receiver
from src.pixxi.http import PixxiHttp
from src.pixxi.read_tools import ReadTools
from src.pixxi.write_client import WriteClient

TOKEN = "test-token"
SECRET = "test-webhook-secret"
RECEIVER_BASE = "http://receiver.test"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        pixxi_base_url="http://testserver",
        pixxi_token=TOKEN,
        pixxi_min_read_interval=0,
        webhook_secret=SECRET,
        webhook_public_base_url=RECEIVER_BASE,
        audit_log=tmp_path / "audit" / "writes.jsonl",
        state_db=tmp_path / "middleware.sqlite",
    )


@pytest.fixture
def store() -> MiddlewareStore:
    s = MiddlewareStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def receiver(settings, store):
    app = create_receiver(settings=settings, store=store)
    with TestClient(app) as client:
        client.app_state = app.state
        yield client


@pytest.fixture
def mock_app(receiver):
    def dispatch(url: str, payload: dict) -> None:
        parts = urlsplit(url)
        receiver.post(f"{parts.path}?{parts.query}", json=payload)

    return create_mock(dispatcher=dispatch)


@pytest.fixture
def mock_client(mock_app):
    with TestClient(mock_app) as client:
        yield client


@pytest.fixture
def http(settings, mock_client) -> PixxiHttp:
    return PixxiHttp(settings, client=mock_client)


@pytest.fixture
def read_tools(settings, http) -> ReadTools:
    return ReadTools(settings=settings, http=http)


@pytest.fixture
def write_client(settings, store, http) -> WriteClient:
    return WriteClient(store=store, settings=settings, http=http)


@pytest.fixture
def subscribed(http):
    """Register the receiver for every event, as scripts/subscribe_webhooks.py would."""
    for event in EVENT_SLUGS.values():
        http.request("POST", "/pixxiapi/webhook/v1/subscribe", kind="write",
                     json={"event": event, "callbackUrl": f"{RECEIVER_BASE}{callback_path(event)}?key={SECRET}"})
    return True
