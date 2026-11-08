"""Fixed-route orchestrator (step 4): routing, error handling and the commit rules. No LLM calls."""

from src.agents.base import AgentResult
from src.audit.note import AgentNote
from src.ingest.webhook_receiver import WebhookEvent
from src.orchestrator.graph import Orchestrator


def test_events_without_a_route_do_nothing(make_pipeline):
    p = make_pipeline()
    run = p.orchestrator.handle(WebhookEvent(event="UPDATE_LEADS", payload={}, lead_id=910005))
    assert run["outcome"] == "no_route"
    assert p.llm.calls_made == 0 and p.store.writes() == []


def test_agent_failure_writes_nothing(make_pipeline, settings):
    # Test settings carry no API key, so Agent 1's LLM call fails; the run must stop cleanly.
    p = make_pipeline()
    before = len(p.state.logs)
    run = p.orchestrator.handle(WebhookEvent(event="ADD_LEADS", payload={}, lead_id=910005))
    assert run["outcome"] == "error" and "OPENAI_API_KEY" in run["detail"]
    assert len(p.state.logs) == before and p.store.writes() == []
    assert (settings.eval_runs_dir / "orchestrator_runs.jsonl").exists()


class RogueAgent:
    """Returns a note for a different lead than the one the run is about."""

    agent_id = "agent_1_intake"

    def run(self, state, tools, **_):
        return AgentResult(notes=[AgentNote(agent_id="agent_1_intake", run_id="x", lead_id=999,
                                            summary="s", rationale="r")])


def test_commit_refuses_writes_to_another_lead(read_tools, write_client, store):
    orch = Orchestrator(read_tools, write_client, {"agent_1_intake": RogueAgent()})
    run = orch.handle(WebhookEvent(event="ADD_LEADS", payload={}, lead_id=900001))
    assert run["outcome"] == "error" and "commit refused" in run["detail"]
    assert store.writes() == []
