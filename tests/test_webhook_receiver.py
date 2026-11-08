"""Build step 3 'done when': a posted note is tagged and the receiver ignores the event it caused.

The mock fires webhooks the worst-case way (our note -> ADD_LEAD_NOTE + UPDATE_LEADS), and
the dispatcher in conftest.py delivers them to the receiver in-process.
"""

from datetime import datetime, timedelta, timezone

from tests.conftest import SECRET
from tests.test_write_client import note, reminder


def decisions(store):
    return [(r["event"], r["decision"], r["reason"]) for r in store.webhook_log()]


def test_own_note_events_are_ignored(subscribed, write_client, store, receiver):
    write_client.add_note(note(900001))
    log = decisions(store)
    assert ("ADD_LEAD_NOTE", "ignored", "self-write: note carries the AI tag") in log
    assert any(e == "UPDATE_LEADS" and d == "ignored" and r.startswith("self-write") for e, d, r in log)
    assert receiver.app_state.accepted == []


def test_own_reminder_events_are_ignored(subscribed, write_client, store, receiver):
    write_client.create_reminder(reminder(900001))
    log = decisions(store)
    assert ("ADD_REMINDER", "ignored", "self-write: reminder carries the AI tag") in log
    assert any(e == "UPDATE_LEADS" and d == "ignored" for e, d, _ in log)
    assert receiver.app_state.accepted == []


def test_human_note_is_accepted(subscribed, mock_client, store, receiver):
    mock_client.post("/mock/leads/900002/notes", json={"message": "Viewing booked for Saturday."})
    accepted = receiver.app_state.accepted
    assert [e.event for e in accepted] == ["ADD_LEAD_NOTE", "UPDATE_LEADS"]
    assert all(e.lead_id == 900002 for e in accepted)


def test_new_lead_is_accepted(subscribed, mock_client, receiver):
    created = mock_client.post("/mock/leads", json={"name": "Synthetic Lead 006", "clientSource": "PROPERTY_FINDER"}).json()
    accepted = receiver.app_state.accepted
    assert len(accepted) == 1 and accepted[0].event == "ADD_LEADS" and accepted[0].lead_id == created["id"]


def test_human_activity_right_after_our_write(subscribed, write_client, mock_client, receiver):
    """Known trade-off of the worst-case loop guard: within the window, the human's
    UPDATE_LEADS is suppressed too, but the ADD_LEAD_NOTE carrying the human's note still gets through."""
    write_client.add_note(note(900002))
    mock_client.post("/mock/leads/900002/notes", json={"message": "Client confirmed mortgage pre-approval."})
    assert [e.event for e in receiver.app_state.accepted] == ["ADD_LEAD_NOTE"]


def test_update_after_window_is_accepted(receiver, store, settings):
    old = datetime.now(timezone.utc) - timedelta(seconds=settings.self_write_window_seconds + 5)
    store.record_write(900003, "note", "agent_x", "run", at=old)
    payload = {"id": 900003, "updateTime": "2026-11-08 10:00:00"}
    r = receiver.post(f"/webhooks/pixxi/update-leads?key={SECRET}", json=payload)
    assert r.json()["status"] == "accepted"


def test_duplicate_delivery_is_ignored(receiver):
    payload = {"id": 900004, "updateTime": "2026-11-08 10:00:00"}
    first = receiver.post(f"/webhooks/pixxi/update-leads?key={SECRET}", json=payload).json()
    second = receiver.post(f"/webhooks/pixxi/update-leads?key={SECRET}", json=payload).json()
    assert first["status"] == "accepted"
    assert second == {"status": "ignored", "reason": "duplicate delivery"}


def test_bad_key_and_unknown_event_are_rejected(receiver):
    assert receiver.post("/webhooks/pixxi/add-leads?key=wrong", json={"id": 1}).status_code == 403
    assert receiver.post("/webhooks/pixxi/add-leads", json={"id": 1}).status_code == 403
    assert receiver.post(f"/webhooks/pixxi/nope?key={SECRET}", json={"id": 1}).status_code == 404
    assert receiver.post(f"/webhooks/pixxi/add-leads?key={SECRET}", json=[1, 2]).status_code == 400
