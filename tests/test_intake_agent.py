"""Agent 1: the deterministic parts (facts, checks, note) and the skip path. No LLM calls."""

import json

from src.agents.intake import (
    AGENT_ID,
    IntakeAgent,
    IntakeHypothesis,
    build_note,
    check_listing,
    listing_facts,
    missing_fields,
    portal_facts,
)
from src.audit.note import parse_message
from src.llm.service import LLMService
from src.pixxi.models import Lead, Listing
from tests.conftest import SEED_DIR


def _load(name):
    return json.loads((SEED_DIR / f"{name}.json").read_text(encoding="utf-8"))


LEADS = {l["id"]: l for l in _load("leads") + _load("arrivals")}
LISTINGS = {p["id"]: p for p in _load("listings")}


def lead(lead_id) -> Lead:
    return Lead.model_validate(LEADS[lead_id])


def listing_of(lead_id) -> Listing:
    return Listing.model_validate(LISTINGS[LEADS[lead_id]["property"]["id"]])


def hypothesis(**kw) -> IntakeHypothesis:
    base = dict(buyer_type="investor", completion_preference="off_plan", financing="unknown",
                listing_fit="inconsistent", confidence="medium",
                headline="Investor, 1-2BR off-plan apartment, AED 1.25-1.71M",
                rationale="Budget and type are given; the enquired listing is above budget.",
                to_confirm=["Purchase timeline?", "Is the budget flexible?", "Cash or mortgage?"])
    return IntakeHypothesis(**{**base, **kw})


def test_llm_never_sees_identity_or_protected_attributes():
    for raw in LEADS.values():
        sent = json.dumps(portal_facts(Lead.model_validate(raw)))
        for field in ("name", "phone", "email", "nationality"):
            assert str(raw[field]) not in sent, field
        assert raw["agentInfo"]["email"] not in sent
        assert "gender" not in sent and "nationality" not in sent


def test_listing_check_flags_the_inconsistent_enquiry():
    # 910008: budget 1.25-1.71M, enquired listing priced 2.28M
    check = check_listing(lead(910008), listing_of(910008), tolerance=0.10)
    assert check["result"] == "outside_budget"
    ok = check_listing(lead(910006), listing_of(910006), tolerance=0.10)
    assert ok["result"] == "within_budget" and ok["region_matches"] is True


def test_listing_check_without_listing_or_budget():
    assert check_listing(lead(910004), None, 0.10) == {"result": "no_listing"}
    assert check_listing(lead(910015), listing_of(910008), 0.10)["result"] == "not_checkable"


def test_sparse_lead_reports_missing_fields():
    assert set(missing_fields(lead(910015))) >= {"budget", "bedrooms", "property type", "payment method"}
    assert "budget" not in missing_fields(lead(910005))


def test_rent_leads_are_not_missing_payment_or_buyer_type():
    rent = lead(910001)
    assert rent.clientType == "RENT" and rent.payMethod is None and rent.buyerType is None
    assert "payment method" not in missing_fields(rent)
    assert "investor or end user" not in missing_fields(rent)


def test_listing_facts_reads_completion_status():
    assert listing_facts(listing_of(910008))["completion_status"] == "OFF_PLAN"
    assert listing_facts(None) is None


def test_note_carries_facts_hypothesis_and_questions():
    l, li = lead(910008), listing_of(910008)
    facts, info = portal_facts(l), listing_facts(li)
    check = check_listing(l, li, 0.10)
    note = build_note(l, hypothesis(headline="x" * 200), facts, info, check, missing_fields(l),
                      run_id="r1", prompt_ref="agent_1_intake/v1", model="gpt-4.1-mini")
    assert note.agent_id == AGENT_ID and note.lead_id == 910008
    assert note.summary.startswith("Layer 1 profile (medium confidence): ")
    assert len(note.summary) < 160  # headline is capped
    assert note.input_signals["listing_check"]["result"] == "outside_budget"
    assert note.input_signals["prompt"] == "agent_1_intake/v1"
    assert note.decision["listing_fit"] == "inconsistent"
    message = note.to_message()
    assert "- Is the budget flexible?" in message
    assert parse_message(message) == note


def test_already_bootstrapped_lead_is_skipped_without_an_llm_call(read_tools, write_client, settings):
    # Lead 900001 exists in the default mock fixtures; give it an earlier Agent 1 note.
    earlier = build_note(Lead(id=900001), hypothesis(), {}, None, {"result": "no_listing"}, [],
                         run_id="r0", prompt_ref="agent_1_intake/v1", model="m")
    write_client.add_note(earlier)
    llm = LLMService(settings.model_copy(update={"llm_max_calls_per_process": 0}), log_path=None)
    result = IntakeAgent(llm, settings).run({"lead_id": 900001}, read_tools, run_id="r1")
    assert result.notes == [] and result.state_updates["agent_1"].startswith("skipped")
    assert llm.calls_made == 0
