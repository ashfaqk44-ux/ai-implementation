"""C7 LLM service pieces that never reach the network."""

import pytest

from src.llm.service import LLMBudgetError, LLMError, LLMService, load_prompt
from src.agents.intake import IntakeHypothesis


def test_agent_1_prompt_loads_with_version():
    assert load_prompt("agent_1_intake_v1").ref == "agent_1_intake/v1"  # kept for the audit trail
    p = load_prompt("agent_1_intake_v2")
    assert p.ref == "agent_1_intake/v2"
    system, user = p.render(portal_facts={"budget_aed": None}, listing=None,
                            listing_check={"result": "no_listing"}, missing_fields=["budget"])
    assert "Layer 1 profile" in system
    assert '"budget_aed": null' in user and "$" not in user


def test_missing_prompt_variable_is_an_error():
    with pytest.raises(KeyError):
        load_prompt("agent_1_intake_v2").render(portal_facts={})


def test_spend_guard_blocks_calls_before_any_request(settings):
    llm = LLMService(settings.model_copy(update={"llm_max_calls_per_process": 0}), log_path=None)
    with pytest.raises(LLMBudgetError):
        llm.structured(load_prompt("agent_1_intake_v2"),
                       {"portal_facts": {}, "listing": None, "listing_check": {}, "missing_fields": []},
                       IntakeHypothesis, run_id="r", agent_id="agent_1_intake")


def test_missing_key_fails_clearly(settings, tmp_path):
    llm = LLMService(settings, log_path=tmp_path / "calls.jsonl")  # test settings carry no key
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        llm.structured(load_prompt("agent_1_intake_v2"),
                       {"portal_facts": {}, "listing": None, "listing_check": {}, "missing_fields": []},
                       IntakeHypothesis, run_id="r", agent_id="agent_1_intake")


def test_cost_estimate(settings):
    llm = LLMService(settings, log_path=None)
    assert llm._cost(1_000_000, 1_000_000) == pytest.approx(0.40 + 1.60)
