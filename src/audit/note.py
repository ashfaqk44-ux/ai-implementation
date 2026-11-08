"""The structured note every agent write carries (README section 6).

Pixxi stores an activity log as a plain `message` string (POST /lead/addLog/{AppName}),
so the note is rendered as readable text for the real estate agent, followed by one
machine-readable trailer line holding the full JSON:

    [AI agent_3_scoring] Score 78 (high confidence)
    Rationale: Confirmed budget and timeline in note of 3 Nov.
    -- pixxi-agent-note/1 {"schema": "pixxi-agent-note/1", "ai_generated": true, ...}

The trailer is what the webhook receiver (self-write filter) and the dormancy logic look
for to tell the system's own writes apart from human activity. ASCII only, so it survives
whatever text handling Pixxi applies.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA = "pixxi-agent-note/1"
TRAILER_PREFIX = f"-- {SCHEMA} "
AI_TITLE_PREFIX = "[AI]"
_TRAILER_RE = re.compile(r"^-- pixxi-agent-note/\d+ (\{.*\})\s*$", re.MULTILINE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_: Literal["pixxi-agent-note/1"] = Field(default=SCHEMA, alias="schema")
    ai_generated: Literal[True] = True
    agent_id: str
    run_id: str
    lead_id: int
    timestamp: datetime = Field(default_factory=_utcnow)
    summary: str  # one-line headline shown first in Pixxi
    input_signals: dict[str, Any] = Field(default_factory=dict)
    decision: dict[str, Any] = Field(default_factory=dict)
    rationale: str

    def to_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json", by_alias=True), ensure_ascii=True, separators=(",", ":")
        )

    def to_message(self) -> str:
        return (
            f"[AI {self.agent_id}] {self.summary}\n"
            f"Rationale: {self.rationale}\n"
            f"{TRAILER_PREFIX}{self.to_json()}"
        )


def parse_message(message: str | None) -> AgentNote | None:
    """Return the AgentNote embedded in a Pixxi message, or None for human-written text."""
    if not message:
        return None
    match = _TRAILER_RE.search(message)
    if not match:
        return None
    try:
        return AgentNote.model_validate(json.loads(match.group(1)))
    except (ValueError, TypeError):
        return None


def is_ai_message(text: str | None) -> bool:
    """True if the text was written by this middleware (note body or reminder title/description)."""
    if not text:
        return False
    return parse_message(text) is not None or text.lstrip().startswith(AI_TITLE_PREFIX)
