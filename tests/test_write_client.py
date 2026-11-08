import json
from datetime import datetime, timedelta, timezone

import pytest

from src.agents.base import ReminderRequest
from src.audit.note import AgentNote, parse_message
from src.pixxi.write_client import WRITE_PATHS, UntaggedWriteError, WriteClient


def note(lead_id=900001) -> AgentNote:
    return AgentNote(agent_id="agent_1_intake", run_id="test-0001", lead_id=lead_id,
                     summary="Layer 1 profile created", input_signals={"source": "PROPERTY_FINDER"},
                     decision={"layer": 1}, rationale="Built from portal fields and the referenced listing.")


def reminder(lead_id=900001) -> ReminderRequest:
    return ReminderRequest(agent_id="agent_3_scoring", run_id="test-0002", lead_id=lead_id,
                           participants=[2031], title="Call lead within 4 hours",
                           start_time=datetime.now(timezone.utc) + timedelta(hours=1),
                           rationale="High intent portal lead; respond inside the recommended window.")


def test_note_is_posted_tagged(write_client, mock_app, read_tools, store, settings):
    write_client.add_note(note())
    logs = read_tools.list_activity_logs(900001).items
    assert len(logs) == 1
    parsed = parse_message(logs[0].message)
    assert parsed is not None and parsed.ai_generated and parsed.agent_id == "agent_1_intake"
    assert [w["kind"] for w in store.writes()] == ["note"]
    audit = [json.loads(l) for l in settings.audit_log.read_text(encoding="utf-8").splitlines()]
    assert audit[0]["kind"] == "note" and audit[0]["agent_id"] == "agent_1_intake"


def test_reminder_is_posted_tagged_and_linked_to_lead(write_client, mock_app):
    write_client.create_reminder(reminder())
    state = mock_app.state.mock
    r = state.reminders[0]
    assert r["title"].startswith("[AI] ")
    assert r["object_type"] == "LEADS_REMINDER" and r["object_id"] == 900001
    assert r["object_module"] == "Buy Lead"
    assert parse_message(r["description"]).ai_generated
    assert state.leads[900001]["reminderCount"] == 1


def test_untagged_writes_are_rejected(write_client):
    with pytest.raises(UntaggedWriteError):
        write_client.add_note({"leadId": 900001, "message": "hello"})
    with pytest.raises(UntaggedWriteError):
        write_client.create_reminder({"title": "x"})
    assert write_client.http.calls == []


def test_only_write_paths_and_never_forbidden_endpoints(write_client, mock_app, settings):
    write_client.add_note(note())
    write_client.create_reminder(reminder())
    called = {(m, p.replace(settings.pixxi_app_name, "{AppName}")) for m, p in write_client.http.calls}
    assert called <= WRITE_PATHS
    assert mock_app.state.mock.forbidden_calls == []


def test_write_client_has_no_update_delete_or_reassign_methods():
    forbidden = ("put", "patch", "delete", "update", "assign", "remove", "edit")
    public = [n.lower() for n in dir(WriteClient) if not n.startswith("_")]
    assert not [n for n in public if any(w in n for w in forbidden)]
