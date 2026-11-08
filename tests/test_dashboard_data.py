"""Data layer of the live dashboard (ui/data.py). No LLM calls, no Streamlit."""

import json
import random
from datetime import datetime, timedelta

import pytest

from src.agents.intake import AGENT_ID
from src.audit.note import AgentNote
from src.audit.store import MiddlewareStore
from src.pixxi.models import PIXXI_TZ, Lead
from ui.data import (
    MockControl,
    count_ai_notes_written,
    dashboard_settings,
    fetch_leads,
    has_layer1_note,
    lead_notes,
    lead_row,
    llm_usage,
    load_arrivals,
    new_in_last,
    next_arrival,
    read_jsonl_tail,
    read_sqlite_rows,
)


def ai_note(lead_id: int, agent_id: str = AGENT_ID) -> AgentNote:
    return AgentNote(agent_id=agent_id, run_id="r1", lead_id=lead_id, summary="Layer 1 profile (medium confidence): x",
                     rationale="because", action_items=["Timeline?"], decision={"layer": 1})


def test_mock_mode_drops_read_spacing(settings):
    assert dashboard_settings(settings.model_copy(update={"pixxi_min_read_interval": 2.0})).pixxi_min_read_interval == 0
    real = settings.model_copy(update={"pixxi_mode": "real", "pixxi_min_read_interval": 2.0})
    assert dashboard_settings(real).pixxi_min_read_interval == 2.0


def test_leads_newest_first_and_rows_hide_personal_data(read_tools):
    leads, total = fetch_leads(read_tools)
    assert total == len(leads) and len(leads) >= 2
    times = [l.createTime for l in leads]
    assert times == sorted(times, reverse=True)
    row = lead_row(leads[0], layer1=True)
    assert row["Layer 1 note"] == "✅"
    for field in ("name", "phone", "email", "nationality", "gender"):
        assert field not in row


def test_notes_split_ai_and_human(read_tools, write_client, mock_client):
    write_client.add_note(ai_note(900001))
    mock_client.post("/mock/leads/900001/notes", json={"message": "Called the client, budget firm."})
    notes = lead_notes(read_tools, 900001)
    ai = [n for n in notes if n.ai is not None]
    human = [n for n in notes if n.ai is None]
    assert len(ai) == 1 and ai[0].human_text.startswith("[AI agent_1_intake]")
    assert "pixxi-agent-note" not in ai[0].human_text
    assert any(n.text == "Called the client, budget firm." for n in human)
    assert has_layer1_note(notes)
    assert not has_layer1_note([n for n in notes if n.ai is None])


def test_new_in_last_24h():
    now = datetime(2026, 11, 9, 12, 0, tzinfo=PIXXI_TZ)
    leads = [Lead(id=1, createTime=now - timedelta(hours=2)), Lead(id=2, createTime=now - timedelta(days=3))]
    assert new_in_last(leads, now=now) == 1


def test_sqlite_reader_is_read_only_and_tolerates_missing_files(tmp_path):
    db = tmp_path / "state.sqlite"
    assert read_sqlite_rows(db, "webhook_log") == [] and not db.exists()  # never creates the file
    store = MiddlewareStore(db)
    store.log_webhook("ADD_LEADS", "910001", "accepted", "new event", {"id": 910001})
    store.log_webhook("ADD_LEAD_NOTE", "5", "ignored", "self-write: note carries the AI tag", {})
    store.record_write(910001, "note", AGENT_ID, "r1")
    store.close()
    rows = read_sqlite_rows(db, "webhook_log")
    assert [r["event"] for r in rows] == ["ADD_LEAD_NOTE", "ADD_LEADS"]  # newest first
    assert "payload" not in rows[0]
    assert count_ai_notes_written(db) == 1
    with pytest.raises(ValueError):
        read_sqlite_rows(db, "sqlite_master")


def test_jsonl_tail_and_llm_usage(tmp_path):
    path = tmp_path / "llm_calls.jsonl"
    rows = [{"ok": True, "input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.0007, "model": "gpt-4.1-mini",
             "prompt": "agent_1_intake/v2"},
            {"ok": False, "input_tokens": None, "output_tokens": None, "cost_usd": None, "model": "gpt-4.1-mini",
             "prompt": "agent_1_intake/v2"}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    usage = llm_usage(path)
    assert usage["calls"] == 2 and usage["errors"] == 1
    assert usage["input_tokens"] == 1000 and usage["cost_usd"] == pytest.approx(0.0007)
    assert usage["last_prompt"] == "agent_1_intake/v2"
    assert read_jsonl_tail(path, limit=5)[0]["ok"] is False  # newest first, bad line skipped
    assert llm_usage(tmp_path / "missing.jsonl")["calls"] == 0


def test_next_arrival_uses_unseen_seed_leads_then_copies():
    arrivals = load_arrivals()
    assert len(arrivals) == 5
    first = next_arrival(arrivals, existing=[])
    assert first["id"] == arrivals[0]["id"] and "createTime" not in first
    second = next_arrival(arrivals, existing=[Lead(id=arrivals[0]["id"])])
    assert second["id"] == arrivals[1]["id"]
    everything = [Lead(id=a["id"], name=a["name"]) for a in arrivals] + [Lead(id=1, name="Synthetic Lead X01")]
    extra = next_arrival(arrivals, existing=everything, rng=random.Random(0))
    assert "id" not in extra and extra["name"] == "Synthetic Lead X02"


def test_mock_control_simulates_humans_and_refuses_real_mode(settings, mock_client, read_tools):
    control = MockControl(settings, client=mock_client)
    created = control.new_lead(next_arrival(load_arrivals(), existing=[]))
    assert created["id"] == load_arrivals()[0]["id"]
    assert read_tools.find_lead(created["id"]) is not None
    control.human_note(created["id"], "Client confirmed budget.")
    assert any(n.text == "Client confirmed budget." for n in lead_notes(read_tools, created["id"]))

    real = MockControl(settings.model_copy(update={"pixxi_mode": "real"}), client=mock_client)
    with pytest.raises(RuntimeError):
        real.new_lead({})
