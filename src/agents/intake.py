"""Agent 1: lead intake and Layer 1 profile bootstrap (README section 3).

Trigger: ADD_LEADS. Needs nothing from other agents.

Steps:
1. Perceive: read the lead (lead/list search by id), the interested listing from the lead's
   `property` object (GET /pixxiapi/v1/{propertyId}), and the lead's activity logs.
2. Skip if this agent already bootstrapped the lead (an earlier AI note from agent_1_intake),
   so a repeated or duplicated ADD_LEADS event never costs a second LLM call or a second note.
3. Code extracts the portal facts, checks the listing price against the budget (+/- the
   configured tolerance) and lists missing fields. These are facts, not judgments.
4. The LLM forms the hypothesis (buyer type, completion preference, financing, fit with
   the listing, confidence) and the questions the real estate agent should ask.
5. Return one AI-tagged note (summary, rationale, questions, full audit JSON). The
   orchestrator posts it; this agent never writes to Pixxi.

Privacy and bias: the LLM never sees name, phone, e-mail, nationality, gender, birthdate or
the assigned agent's details; only the fields listed in `portal_facts`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.agents.base import AgentResult
from src.audit.note import AgentNote, parse_message
from src.config import Settings, get_settings
from src.llm.service import LLMService, load_prompt
from src.orchestrator.state import LeadState
from src.pixxi.models import Lead, Listing
from src.pixxi.read_tools import PixxiError, ReadTools

AGENT_ID = "agent_1_intake"
PROMPT_NAME = "agent_1_intake_v2"  # file in src/llm/prompts/; v1 kept for the audit trail

# Portal fields that matter for a Layer 1 profile; a None value is reported as missing.
# Payment method and investor/end-user status do not apply to RENT leads.
RENT_NOT_APPLICABLE = {"payMethod", "buyerType"}
PROFILE_FIELDS = {
    "budget": "budget",
    "rooms": "bedrooms",
    "houseType": "property type",
    "region": "location",
    "projectType": "off-plan or ready",
    "payMethod": "payment method",
    "buyerType": "investor or end user",
}


class IntakeHypothesis(BaseModel):
    """What the LLM returns (structured output)."""

    buyer_type: Literal["investor", "end_user", "unknown"] = Field(
        description="Investor or end user (home buyer/tenant), from buyerType, project type and enquiry")
    completion_preference: Literal["off_plan", "ready", "either", "unknown"]
    financing: Literal["cash", "mortgage", "other", "unknown"] = Field(
        description="From payMethod only; unknown if missing")
    listing_fit: Literal["consistent", "partly_consistent", "inconsistent", "no_listing"] = Field(
        description="Does the enquired listing fit the lead's stated budget, type, bedrooms and location?")
    confidence: Literal["low", "medium", "high"]
    headline: str = Field(description="One line, max 110 characters, e.g. 'Investor, 1-2BR off-plan "
                                      "apartment, Business Bay, AED 1.2-1.7M'")
    rationale: str = Field(description="2-3 sentences: which facts support the hypothesis and what is uncertain")
    to_confirm: list[str] = Field(description="2 to 5 short questions for the real estate agent to ask the client")


def _fmt_aed(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value / 1_000_000:.2f}M" if value >= 1_000_000 else f"{value / 1_000:.0f}K"


def portal_facts(lead: Lead) -> dict[str, Any]:
    """The lead fields the agent may use. Excludes identity, contact and protected attributes."""
    rooms_low, rooms_high = lead.rooms_range
    size_low, size_high = lead.size_range
    return {
        "lead_id": lead.id,
        "client_type": lead.clientType,
        "source": lead.clientSource,
        "created": lead.createTime.isoformat() if lead.createTime else None,
        "budget_aed": None if lead.budget is None else {"min": lead.budget_min, "max": lead.budget_max},
        "bedrooms": None if lead.rooms is None else {"min": rooms_low, "max": rooms_high},
        "size_sqft": None if lead.size is None else {"min": size_low, "max": size_high},
        "property_types": lead.houseType,
        "project_types": lead.projectType,
        "pay_method": lead.payMethod,
        "buyer_type_field": lead.buyerType,
        "region": lead.region,
        "community": lead.community,
        "portal_notes": lead.notes,
    }


def missing_fields(lead: Lead) -> list[str]:
    rent = (lead.clientType or "").upper() == "RENT"
    return [label for field, label in PROFILE_FIELDS.items()
            if not getattr(lead, field) and not (rent and field in RENT_NOT_APPLICABLE)]


def listing_facts(listing: Listing | None) -> dict[str, Any] | None:
    if listing is None:
        return None
    extra = listing.model_extra or {}
    completion = next((extra[k].get("completionStatus") for k in ("newParam", "sellParam", "rentParam")
                       if isinstance(extra.get(k), dict)), None)
    return {
        "listing_id": listing.id,
        "reference": listing.propertyId,
        "title": listing.title,
        "listing_type": listing.listingType,
        "property_types": listing.propertyType,
        "price_aed": listing.price,
        "bedrooms": listing.bedRooms,
        "size_sqft": listing.size,
        "region": listing.region,
        "community": listing.community,
        "completion_status": completion,
    }


def check_listing(lead: Lead, listing: Listing | None, tolerance: float) -> dict[str, Any]:
    """Deterministic price-vs-budget check with the configured tolerance (Section 3.12)."""
    if listing is None:
        return {"result": "no_listing"}
    if listing.price is None or lead.budget_max is None:
        return {"result": "not_checkable", "reason": "listing price or lead budget missing"}
    low = (lead.budget_min or 0) * (1 - tolerance)
    high = lead.budget_max * (1 + tolerance)
    within = low <= listing.price <= high
    return {
        "result": "within_budget" if within else "outside_budget",
        "price_aed": listing.price,
        "budget_band_aed": [round(low), round(high)],
        "tolerance": tolerance,
        "region_matches": (listing.region or "").lower() == (lead.region or "").lower() if lead.region else None,
    }


def already_bootstrapped(tools: ReadTools, lead_id: int) -> bool:
    for log in tools.iter_activity_logs(lead_id, log_type="Note"):
        note = parse_message(log.message)
        if note is not None and note.agent_id == AGENT_ID:
            return True
    return False


def build_note(
    lead: Lead,
    hypothesis: IntakeHypothesis,
    facts: dict[str, Any],
    listing: dict[str, Any] | None,
    check: dict[str, Any],
    missing: list[str],
    *,
    run_id: str,
    prompt_ref: str,
    model: str,
) -> AgentNote:
    """Turn the LLM hypothesis plus the code facts into the audited note."""
    headline = hypothesis.headline.strip().rstrip(".")
    if len(headline) > 110:
        headline = headline[:107].rstrip() + "..."
    questions = [q.strip() for q in hypothesis.to_confirm if q.strip()][:5]
    return AgentNote(
        agent_id=AGENT_ID,
        run_id=run_id,
        lead_id=lead.id,
        summary=f"Layer 1 profile ({hypothesis.confidence} confidence): {headline}",
        rationale=hypothesis.rationale.strip(),
        action_items=questions,
        input_signals={
            "layer": 1,
            "source": facts.get("source"),
            "budget_aed": facts.get("budget_aed"),
            "listing_id": listing["listing_id"] if listing else None,
            "listing_check": check,
            "missing_fields": missing,
            "prompt": prompt_ref,
            "model": model,
        },
        decision={
            "layer": 1,
            "buyer_type": hypothesis.buyer_type,
            "completion_preference": hypothesis.completion_preference,
            "financing": hypothesis.financing,
            "listing_fit": hypothesis.listing_fit,
            "confidence": hypothesis.confidence,
        },
    )


class IntakeAgent:
    agent_id = AGENT_ID

    def __init__(self, llm: LLMService, settings: Settings | None = None):
        self.llm = llm
        self.settings = settings or get_settings()
        self.prompt = load_prompt(PROMPT_NAME)

    def run(self, state: LeadState, tools: ReadTools, *, run_id: str, scenario_id: str | None = None) -> AgentResult:
        lead_id = state["lead_id"]
        if already_bootstrapped(tools, lead_id):
            return AgentResult(state_updates={"agent_1": "skipped: lead already has a Layer 1 note"})

        lead = tools.find_lead(lead_id)
        if lead is None:
            return AgentResult(state_updates={"agent_1": f"skipped: lead {lead_id} not found"})

        listing: Listing | None = None
        listing_ref = (lead.interested_property or {}).get("id")
        if listing_ref is not None:
            try:
                listing = tools.get_listing(listing_ref)
            except PixxiError:
                listing = None  # listing withdrawn or unknown: profile from portal data only

        facts = portal_facts(lead)
        listing_info = listing_facts(listing)
        check = check_listing(lead, listing, self.settings.budget_tolerance)
        missing = missing_fields(lead)

        hypothesis, record = self.llm.structured(
            self.prompt,
            {"portal_facts": facts, "listing": listing_info, "listing_check": check, "missing_fields": missing},
            IntakeHypothesis,
            run_id=run_id,
            agent_id=AGENT_ID,
            scenario_id=scenario_id,
        )
        note = build_note(lead, hypothesis, facts, listing_info, check, missing,
                          run_id=run_id, prompt_ref=self.prompt.ref, model=record.model)
        profile = {"layer": 1, **note.decision, "missing_fields": missing, "listing_check": check["result"]}
        return AgentResult(
            notes=[note],
            state_updates={"layer": 1, "profile": profile, "agent_1": "bootstrapped"},
        )
