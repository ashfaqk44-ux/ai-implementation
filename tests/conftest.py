"""Shared fixtures: mock Pixxi and the webhook receiver wired together in-process.

When the mock fires a webhook, the dispatcher below delivers it straight to the receiver's
TestClient, so the full loop (write -> Pixxi event -> receiver decision) runs without network.
"""

from __future__ import annotations

from pathlib import Path
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
        eval_runs_dir=tmp_path / "runs",
        # Never pick up a real key from the environment: offline tests must not call the API.
        llm_provider="openai",
        llm_model="gpt-4.1-mini",
        openai_api_key=None,
        anthropic_api_key=None,
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


# -- step 4: seed dataset + receiver + orchestrator, wired in-process ----------------------
SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic" / "seed"


class Pipeline:
    """Seed-data mock -> webhook receiver -> orchestrator -> agents -> write client -> mock."""

    def __init__(self, settings: Settings, store: MiddlewareStore, llm):
        from src.main import build_orchestrator

        self.orchestrator = None
        receiver_app = create_receiver(settings=settings, store=store,
                                       handler=lambda event: self.orchestrator.handle(event))
        self.receiver = TestClient(receiver_app).__enter__()

        def dispatch(url: str, payload: dict) -> None:
            parts = urlsplit(url)
            self.receiver.post(f"{parts.path}?{parts.query}", json=payload)

        self.mock_app = create_mock(dispatcher=dispatch, fixtures_dir=SEED_DIR)
        self.mock = TestClient(self.mock_app).__enter__()
        http = PixxiHttp(settings, client=self.mock)
        self.read_tools = ReadTools(settings=settings, http=http)
        self.write_client = WriteClient(store=store, settings=settings, http=http)
        self.llm = llm
        self.store = store
        self.orchestrator = build_orchestrator(settings, store, self.read_tools, self.write_client, llm)
        for event in EVENT_SLUGS.values():
            http.request("POST", "/pixxiapi/webhook/v1/subscribe", kind="write",
                         json={"event": event, "callbackUrl": f"{RECEIVER_BASE}{callback_path(event)}?key={SECRET}"})

    @property
    def state(self):
        return self.mock_app.state.mock

    def notes_for(self, lead_id: int) -> list[dict]:
        return [log for log in self.state.logs if log["leadId"] == lead_id]

    def close(self) -> None:
        self.mock.__exit__(None, None, None)
        self.receiver.__exit__(None, None, None)


@pytest.fixture
def make_pipeline(settings, store):
    from src.llm.service import LLMService

    created: list[Pipeline] = []

    def make(llm=None, settings_override: Settings | None = None) -> Pipeline:
        s = settings_override or settings
        p = Pipeline(s, store, llm or LLMService(s, log_path=s.eval_runs_dir / "llm_calls.jsonl"))
        created.append(p)
        return p

    yield make
    for p in created:
        p.close()
