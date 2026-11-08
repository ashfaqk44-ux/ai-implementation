"""Wires the middleware together: webhook receiver (C3) -> orchestrator (C4) -> agents.

Run (with the mock on port 8001, see CHAT_HISTORY.md / README):
    uv run uvicorn src.main:create_app --factory --port 8002

This replaces `src.ingest.webhook_receiver:create_app` once agents should actually run:
accepted events are handed to the orchestrator instead of only being recorded.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from src.agents.intake import IntakeAgent
from src.audit.store import MiddlewareStore
from src.config import Settings, get_settings
from src.ingest.webhook_receiver import create_app as create_receiver
from src.llm.service import LLMService
from src.orchestrator.graph import Orchestrator
from src.pixxi.read_tools import ReadTools
from src.pixxi.write_client import WriteClient


def build_orchestrator(
    settings: Settings,
    store: MiddlewareStore,
    read_tools: ReadTools | None = None,
    write_client: WriteClient | None = None,
    llm: LLMService | None = None,
) -> Orchestrator:
    read_tools = read_tools or ReadTools(settings)
    write_client = write_client or WriteClient(store=store, settings=settings)
    llm = llm or LLMService(settings)
    agents = {IntakeAgent.agent_id: IntakeAgent(llm, settings)}
    return Orchestrator(read_tools, write_client, agents,
                        run_log=settings.eval_runs_dir / "orchestrator_runs.jsonl")


def create_app(settings: Settings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = settings or get_settings()
    store = MiddlewareStore(settings.state_db)
    orchestrator = build_orchestrator(settings, store)
    app = create_receiver(settings=settings, store=store, handler=orchestrator.handle)
    app.state.orchestrator = orchestrator
    return app
