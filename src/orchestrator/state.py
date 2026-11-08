"""Lead state carried through the orchestrator graph (README section 6)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict


class LeadState(TypedDict, total=False):
    lead_id: int
    layer: Literal[1, 2]  # data layer (1 = portal hypothesis, 2 = confirmed), not an architecture tier
    profile: dict  # Agent 1 and Agent 2 output
    score: dict | None  # Agent 3 output
    priority: dict | None  # M1 output: tier, window, rationale
    last_human_activity: datetime | None  # ignores ai_generated notes
    pending_requests: list[str]  # e.g. "agent_3: rescore"
    trigger: str  # webhook event name or "SCHEDULER"
