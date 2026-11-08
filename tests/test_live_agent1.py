"""Step 4 'done when': a synthetic ADD_LEADS event produces a structured bootstrap note.

LIVE: calls the real LLM once (about 1-2K tokens). Deselected by default; run with
    uv run pytest -m live
"""

import json

import pytest

from src.audit.note import parse_message
from src.config import Settings
from src.ingest.webhook_receiver import WebhookEvent
from tests.conftest import SEED_DIR

pytestmark = pytest.mark.live


@pytest.fixture
def live_settings(settings) -> Settings:
    real = Settings()  # reads .env
    if real.llm_api_key is None or not real.llm_api_key.get_secret_value().strip():
        pytest.skip("no LLM API key in .env")
    return settings.model_copy(update={
        "llm_provider": real.llm_provider, "llm_model": real.llm_model,
        "openai_api_key": real.openai_api_key, "anthropic_api_key": real.anthropic_api_key,
        "llm_max_calls_per_process": 2,
    })


def test_add_leads_event_produces_a_bootstrap_note(make_pipeline, live_settings):
    p = make_pipeline(settings_override=live_settings)
    arrival = next(l for l in json.loads((SEED_DIR / "arrivals.json").read_text(encoding="utf-8"))
                   if l["id"] == 910008)  # budget below the enquired listing's price

    # A new lead arrives in Pixxi -> ADD_LEADS webhook -> receiver -> orchestrator -> Agent 1
    p.mock.post("/mock/leads", json=arrival)

    run = p.orchestrator.runs[0]
    assert run["event"] == "ADD_LEADS" and run["outcome"] == "completed", run.get("detail")
    notes = [parse_message(log["message"]) for log in p.notes_for(910008)]
    notes = [n for n in notes if n is not None]
    assert len(notes) == 1
    note = notes[0]
    assert note.agent_id == "agent_1_intake" and note.ai_generated is True
    assert note.summary.startswith("Layer 1 profile (")
    assert 2 <= len(note.action_items) <= 5
    assert note.input_signals["listing_check"]["result"] == "outside_budget"
    assert note.decision["listing_fit"] in ("inconsistent", "partly_consistent")
    assert note.input_signals["model"] == live_settings.llm_model

    # The note we wrote fired ADD_LEAD_NOTE and UPDATE_LEADS; the receiver ignored both.
    decisions = [(r["event"], r["decision"]) for r in p.store.webhook_log()]
    assert ("ADD_LEADS", "accepted") in decisions
    assert ("ADD_LEAD_NOTE", "ignored") in decisions and ("UPDATE_LEADS", "ignored") in decisions
    assert len(p.orchestrator.runs) == 1  # no loop

    # A repeated ADD_LEADS for the same lead is skipped without another LLM call.
    again = p.orchestrator.handle(WebhookEvent(event="ADD_LEADS", payload={}, lead_id=910008))
    assert again["outcome"] == "skipped" and p.llm.calls_made == 1
    assert p.state.forbidden_calls == []

    record = p.llm.records[0]
    assert record.ok and record.prompt == "agent_1_intake/v2" and record.input_tokens
    print(f"\n{p.notes_for(910008)[0]['message']}\ncost ~${record.cost_usd} "
          f"({record.input_tokens} in / {record.output_tokens} out, {record.latency_s}s)")
