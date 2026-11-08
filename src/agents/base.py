"""Agent interface (README section 6).

Agents never call the write client themselves. They return an AgentResult and the
orchestrator posts the notes and reminders through C8, so there is one audited exit point.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from src.audit.note import AI_TITLE_PREFIX, AgentNote
from src.orchestrator.state import LeadState


class ReminderRequest(BaseModel):
    """A reminder an agent wants created on a lead (POST /pixxiapi/v1/reminder)."""

    agent_id: str
    run_id: str
    lead_id: int
    client_type: Literal["BUY", "RENT"] = "BUY"
    participants: list[int] = Field(min_length=1)  # Pixxi user IDs (real estate agents)
    title: str
    start_time: datetime
    notify_before_minutes: int | None = 15
    rationale: str
    input_signals: dict[str, Any] = Field(default_factory=dict)
    decision: dict[str, Any] = Field(default_factory=dict)

    def as_note(self) -> AgentNote:
        """The audit note embedded in the reminder description."""
        return AgentNote(
            agent_id=self.agent_id,
            run_id=self.run_id,
            lead_id=self.lead_id,
            summary=f"Reminder: {self.title}",
            input_signals=self.input_signals,
            decision={"reminder_start_time": self.start_time.isoformat(), **self.decision},
            rationale=self.rationale,
        )

    @property
    def marked_title(self) -> str:
        return self.title if self.title.startswith(AI_TITLE_PREFIX) else f"{AI_TITLE_PREFIX} {self.title}"


class AgentResult(BaseModel):
    notes: list[AgentNote] = Field(default_factory=list)
    reminders: list[ReminderRequest] = Field(default_factory=list)
    state_updates: dict[str, Any] = Field(default_factory=dict)
    requests: list[str] = Field(default_factory=list)  # e.g. "agent_3: rescore"


class Agent(Protocol):
    agent_id: str

    def run(self, state: LeadState, tools: Any) -> AgentResult: ...
