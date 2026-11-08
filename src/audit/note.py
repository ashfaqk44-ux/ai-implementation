"""The structured note every agent write carries (README section 6).

Pixxi stores an activity log as a plain `message` string (POST /lead/addLog/{AppName}),
so the note is rendered as readable text for the real estate agent, followed by one
machine-readable trailer line holding the full JSON:

    [AI agent_3_scoring] Score 78 (high confidence)
    Rationale: Confirmed budget and timeline in note of 3 Nov.
    Please confirm with the client and log a note:     (only if action_items is set)
    - ...
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
    # Optional asks for the real estate agent (e.g. Agent 1's details to confirm). Rendered
    # as a bullet list; left out of the JSON when empty so older notes keep the same shape.
    action_items: list[str] = Field(default_factory=list)

    def to_json(self) -> str:
        data = self.model_dump(mode="json", by_alias=True)
        if not data["action_items"]:
            del data["action_items"]
        return json.dumps(data, ensure_ascii=True, separators=(",", ":"))

    def to_message(self) -> str:
        lines = [f"[AI {self.agent_id}] {self.summary}", f"Rationale: {self.rationale}"]
        if self.action_items:
            lines.append("Please confirm with the client and log a note:")
            lines.extend(f"- {item}" for item in self.action_items)
        lines.append(f"{TRAILER_PREFIX}{self.to_json()}")
        return "\n".join(lines)


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
