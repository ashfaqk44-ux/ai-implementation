"""C4 Orchestrator, fixed-route version (build step 4).

A LangGraph StateGraph with three nodes:

    route  ->  run_agents  ->  commit  ->  END
      \\-> END (no route for this event)

- route:      looks up the event in FIXED_ROUTES. Step 8 replaces this table with the
              supervisor LLM (D3: the LLM chooses, code enforces the rules).
- run_agents: runs each routed agent and collects its AgentResult. An agent error stops the
              run before anything is written.
- commit:     the single audited exit point. Code checks every note and reminder (right lead,
              right agent, AI-tagged) and posts it through the write client (C8). Agents never
              write to Pixxi themselves.

Every run appends one row to eval/runs/orchestrator_runs.jsonl. There is no checkpointer
yet; the SQLite checkpoint store (C5) arrives in step 6.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents.base import AgentResult
from src.audit.note import AgentNote
from src.ingest.webhook_receiver import WebhookEvent
from src.orchestrator.state import LeadState
from src.pixxi.read_tools import ReadTools
from src.pixxi.write_client import WriteClient

log = logging.getLogger("pixxi.orchestrator")

# Event -> agents to run, in order. Fixed route for step 4.
FIXED_ROUTES: dict[str, list[str]] = {
    "ADD_LEADS": ["agent_1_intake"],
}


class RunState(TypedDict, total=False):
    run_id: str
    scenario_id: str | None
    event: str
    lead_id: int | None
    lead: LeadState
    route: list[str]
    results: dict[str, dict]  # agent_id -> AgentResult (as dict)
    notes_posted: int
    reminders_posted: int
    outcome: str  # completed | no_route | skipped | error
    detail: str


class CommitRuleError(RuntimeError):
    """An agent returned a write that breaks a hard rule; nothing from the run is posted."""


def new_run_id(event: str, lead_id: int | None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{event.lower()}-{lead_id}-{stamp}-{uuid.uuid4().hex[:6]}"


class Orchestrator:
    def __init__(
        self,
        read_tools: ReadTools,
        write_client: WriteClient,
        agents: dict[str, Any],
        run_log: Path | None = None,
        routes: dict[str, list[str]] | None = None,
    ):
        self.tools = read_tools
        self.writer = write_client
        self.agents = agents
        self.routes = routes or FIXED_ROUTES
        self.run_log = run_log
        self.runs: list[RunState] = []
        self.graph = self._build()

    # -- graph ------------------------------------------------------------------
    def _build(self):
        g = StateGraph(RunState)
        g.add_node("route", self._route)
        g.add_node("run_agents", self._run_agents)
        g.add_node("commit", self._commit)
        g.add_edge(START, "route")
        g.add_conditional_edges("route", lambda s: "run_agents" if s.get("route") else END)
        g.add_conditional_edges("run_agents", lambda s: END if s.get("outcome") == "error" else "commit")
        g.add_edge("commit", END)
        return g.compile()

    def _route(self, state: RunState) -> dict:
        route = [a for a in self.routes.get(state["event"], []) if a in self.agents]
        if not route or state.get("lead_id") is None:
            return {"route": [], "outcome": "no_route",
                    "detail": f"no fixed route for {state['event']} yet" if not route else "event has no lead id"}
        return {"route": route}

    def _run_agents(self, state: RunState) -> dict:
        lead: LeadState = dict(state.get("lead") or {"lead_id": state["lead_id"], "trigger": state["event"]})
        results: dict[str, dict] = {}
        for agent_id in state["route"]:
            try:
                result: AgentResult = self.agents[agent_id].run(
                    lead, self.tools, run_id=state["run_id"], scenario_id=state.get("scenario_id"))
            except Exception as exc:  # stop the run; nothing is written
                log.exception("agent %s failed on lead %s", agent_id, state["lead_id"])
                return {"results": results, "lead": lead, "outcome": "error",
                        "detail": f"{agent_id}: {type(exc).__name__}: {exc}"}
            results[agent_id] = result.model_dump(mode="json", by_alias=True)
            lead.update(result.state_updates)
        return {"results": results, "lead": lead}

    def _commit(self, state: RunState) -> dict:
        lead_id = state["lead_id"]
        to_post: list[tuple[str, Any]] = []
        for agent_id, raw in state.get("results", {}).items():
            result = AgentResult.model_validate(raw)
            for note in result.notes:
                self._check(note.agent_id, note.lead_id, agent_id, lead_id)
                to_post.append(("note", note))
            for reminder in result.reminders:
                self._check(reminder.agent_id, reminder.lead_id, agent_id, lead_id)
                to_post.append(("reminder", reminder))
        notes = reminders = 0
        for kind, item in to_post:
            if kind == "note":
                assert isinstance(item, AgentNote) and item.ai_generated is True
                self.writer.add_note(item)
                notes += 1
            else:
                self.writer.create_reminder(item)
                reminders += 1
        outcome = "completed" if notes or reminders else "skipped"
        detail = "; ".join(str(v) for v in (state.get("lead") or {}).values() if isinstance(v, str)
                           and v.startswith(("skipped", "bootstrapped")))
        return {"notes_posted": notes, "reminders_posted": reminders, "outcome": outcome, "detail": detail}

    @staticmethod
    def _check(item_agent: str, item_lead: int, agent_id: str, lead_id: int) -> None:
        if item_agent != agent_id:
            raise CommitRuleError(f"{agent_id} returned a write labelled as {item_agent}")
        if int(item_lead) != int(lead_id):
            raise CommitRuleError(f"{agent_id} tried to write to lead {item_lead} during a run for lead {lead_id}")

    # -- entry points --------------------------------------------------------------
    def handle(self, event: WebhookEvent, scenario_id: str | None = None) -> RunState:
        """Webhook receiver handler: run the graph for one accepted event."""
        run_id = new_run_id(event.event, event.lead_id)
        initial: RunState = {"run_id": run_id, "scenario_id": scenario_id, "event": event.event,
                             "lead_id": event.lead_id, "notes_posted": 0, "reminders_posted": 0}
        try:
            final: RunState = self.graph.invoke(initial)
        except CommitRuleError as exc:
            final = {**initial, "outcome": "error", "detail": f"commit refused: {exc}"}
        self.runs.append(final)
        self._log(final)
        log.info("run %s %s lead=%s -> %s %s", run_id, event.event, event.lead_id,
                 final.get("outcome"), final.get("detail", ""))
        return final

    def _log(self, final: RunState) -> None:
        if self.run_log is None:
            return
        self.run_log.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **{k: final.get(k) for k in ("run_id", "scenario_id", "event", "lead_id", "route", "outcome",
                                         "detail", "notes_posted", "reminders_posted")},
        }
        with self.run_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
