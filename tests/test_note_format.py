from src.audit.note import AgentNote, is_ai_message, parse_message


def note(**kw) -> AgentNote:
    base = dict(agent_id="agent_3_scoring", run_id="scn04-0017", lead_id=900002,
                summary="Score 78 (high confidence)", input_signals={"layer": 2, "source": "referral"},
                decision={"score": 78, "confidence": "high"},
                rationale="Confirmed budget and timeline in note of 2 Nov.")
    return AgentNote(**{**base, **kw})


def test_message_is_readable_and_round_trips():
    n = note()
    message = n.to_message()
    assert message.startswith("[AI agent_3_scoring] Score 78")
    assert "Rationale: Confirmed budget" in message
    parsed = parse_message(message)
    assert parsed == n
    assert parsed.ai_generated is True


def test_message_is_ascii_even_with_unicode_rationale():
    message = note(rationale="Client prefers café-style layout – near Marina").to_message()
    trailer = message.splitlines()[-1]
    assert trailer.isascii()
    assert parse_message(message).rationale.startswith("Client prefers café")


def test_human_notes_are_not_flagged():
    for text in [
        "Called client, confirmed budget 4.5M cash.",
        "Client asked if we use AI to recommend properties.",
        "-- some dashes in a human note",
        None, "",
    ]:
        assert parse_message(text) is None
        assert is_ai_message(text) is False


def test_reminder_title_marker_is_flagged():
    assert is_ai_message("[AI] Call lead within 4h")


def test_tampered_trailer_is_not_trusted():
    message = note().to_message().replace('"ai_generated":true', '"ai_generated":false')
    assert parse_message(message) is None


def test_action_items_render_as_list_and_round_trip():
    n = note(action_items=["What is the purchase timeline?", "Cash or mortgage?"])
    message = n.to_message()
    assert "Please confirm with the client and log a note:\n- What is the purchase timeline?\n- Cash or mortgage?" in message
    assert parse_message(message) == n


def test_empty_action_items_keep_the_original_json_shape():
    assert '"action_items"' not in note().to_json()
