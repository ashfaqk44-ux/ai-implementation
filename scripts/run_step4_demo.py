"""Step 4 demo: new synthetic leads arrive, Agent 1 writes a Layer 1 bootstrap note for each.

Runs everything in one process (no servers needed): the mock Pixxi loaded with the seed
dataset, the webhook receiver, the fixed-route orchestrator and Agent 1 with the REAL LLM
from .env. Each arrival makes one LLM call (about 1-2K tokens; well under 1 cent with
gpt-4.1-mini).

Usage:
    uv run python -m scripts.run_step4_demo              # all 5 arrivals
    uv run python -m scripts.run_step4_demo --limit 1    # just one (cheapest)
    uv run python -m scripts.run_step4_demo --lead 910008

Logs: eval/runs/llm_calls.jsonl, eval/runs/orchestrator_runs.jsonl, data/audit/writes.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from pydantic import SecretStr

from mock_pixxi.app import create_app as create_mock
from src.audit.note import TRAILER_PREFIX, parse_message
from src.audit.store import MiddlewareStore
from src.config import get_settings
from src.ingest.webhook_receiver import EVENT_SLUGS, callback_path, create_app as create_receiver
from src.llm.service import LLMService
from src.main import build_orchestrator
from src.pixxi.http import PixxiHttp
from src.pixxi.read_tools import ReadTools
from src.pixxi.write_client import WriteClient

SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic" / "seed"
RECEIVER = "http://receiver.local"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="process only the first N arrivals")
    parser.add_argument("--lead", type=int, action="append", help="process only this lead id (repeatable)")
    args = parser.parse_args()

    base = get_settings()
    if base.llm_api_key is None or not base.llm_api_key.get_secret_value().strip():
        print("No LLM API key in .env (OPENAI_API_KEY). Add it and retry.")
        return 1
    secret = "demo-secret"
    settings = base.model_copy(update={"pixxi_base_url": "http://mock.local", "pixxi_token": SecretStr("mock-token"),
                                       "pixxi_min_read_interval": 0, "webhook_secret": SecretStr(secret),
                                       "webhook_public_base_url": RECEIVER})
    # Fresh bookkeeping per demo run, so earlier runs do not trigger the loop guard.
    store = MiddlewareStore(Path(tempfile.mkdtemp()) / "demo.sqlite")

    holder: dict = {}
    receiver = TestClient(create_receiver(settings=settings, store=store,
                                          handler=lambda e: holder["orch"].handle(e, scenario_id="step4-demo")))

    def dispatch(url: str, payload: dict) -> None:
        parts = urlsplit(url)
        receiver.post(f"{parts.path}?{parts.query}", json=payload)

    mock_app = create_mock(dispatcher=dispatch, fixtures_dir=SEED_DIR)
    with receiver, TestClient(mock_app) as mock:
        http = PixxiHttp(settings, client=mock)
        llm = LLMService(settings)
        orch = build_orchestrator(settings, store, ReadTools(settings, http=http),
                                  WriteClient(store=store, settings=settings, http=http), llm)
        holder["orch"] = orch
        for event in EVENT_SLUGS.values():
            http.request("POST", "/pixxiapi/webhook/v1/subscribe", kind="write",
                         json={"event": event, "callbackUrl": f"{RECEIVER}{callback_path(event)}?key={secret}"})

        arrivals = json.loads((SEED_DIR / "arrivals.json").read_text(encoding="utf-8"))
        if args.lead:
            arrivals = [a for a in arrivals if a["id"] in args.lead]
        arrivals = arrivals[: args.limit] if args.limit else arrivals
        print(f"Model: {settings.llm_provider}/{settings.llm_model}   arrivals: {len(arrivals)}\n")

        for lead in arrivals:
            mock.post("/mock/leads", json=lead)  # fires ADD_LEADS
            run = orch.runs[-1]
            print("=" * 100)
            print(f"Lead {lead['id']} ({lead['clientSource']})  ->  {run['outcome']}  {run.get('detail', '')}")
            for log in mock_app.state.mock.logs:
                if log["leadId"] == lead["id"] and parse_message(log["message"]):
                    print(log["message"].split(TRAILER_PREFIX)[0].rstrip())
            print()

        state = mock_app.state.mock
        ignored = sum(1 for r in store.webhook_log() if r["decision"] == "ignored")
        tokens_in = sum(r.input_tokens or 0 for r in llm.records)
        tokens_out = sum(r.output_tokens or 0 for r in llm.records)
        print("=" * 100)
        print(f"LLM calls: {llm.calls_made}   tokens: {tokens_in} in / {tokens_out} out   "
              f"estimated cost: ${llm.total_cost():.4f}")
        print(f"Webhooks: {len(store.webhook_log())} received, {ignored} ignored as self-writes/duplicates")
        print(f"Forbidden PUT/DELETE calls: {len(state.forbidden_calls)}")
    return 0 if all(r["outcome"] == "completed" for r in orch.runs) else 1


if __name__ == "__main__":
    sys.exit(main())
