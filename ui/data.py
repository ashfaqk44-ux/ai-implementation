"""Data access for the live dashboard (ui/dashboard.py). No Streamlit imports, so it is testable.

The dashboard is a viewer, not part of the middleware (C1-C8):
- Pixxi data is read through the existing read tools (C6), so it also works on the real API.
- Middleware state is read from local files: the receiver's SQLite store (opened READ-ONLY)
  and the JSONL run logs in eval/runs/.
- The only writes are to the mock's /mock/* endpoints (simulate a new lead or a human note),
  and only in mock mode. Agent writes still go through the orchestrator and write client.
"""

from __future__ import annotations

import copy
import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from src.audit.note import AgentNote, parse_message
from src.config import Settings, get_settings
from src.pixxi.models import PIXXI_TZ, Lead
from src.pixxi.read_tools import ReadTools

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_ARRIVALS = PROJECT_ROOT / "data" / "synthetic" / "seed" / "arrivals.json"
INTAKE_AGENT_ID = "agent_1_intake"

# Real Pixxi allows 2,000 requests/hour: keep the dashboard's polling well below that.
MIN_REFRESH_SECONDS = {"mock": 2, "real": 15}


def dashboard_settings(base: Settings | None = None) -> Settings:
    """App settings, without the 2 s read spacing when talking to the local mock."""
    s = base or get_settings()
    return s.model_copy(update={"pixxi_min_read_interval": 0}) if s.pixxi_mode == "mock" else s


# -- Pixxi reads ------------------------------------------------------------------------

def fetch_leads(tools: ReadTools, size: int = 50) -> tuple[list[Lead], int | None]:
    """Newest leads first, plus the total number of leads in Pixxi."""
    page = tools.list_leads(sort="CREATE_TIME", sort_type="DESC", size=size)
    return page.items, page.total


@dataclass
class NoteView:
    created: datetime | None
    created_by: int | None
    text: str
    ai: AgentNote | None  # parsed AI note, None for human-written text

    @property
    def human_text(self) -> str:
        """The readable part of the message (AI notes without the JSON trailer)."""
        return self.text.split("\n-- pixxi-agent-note/")[0].strip()


def lead_notes(tools: ReadTools, lead_id: int) -> list[NoteView]:
    """All activity logs of one lead, newest first, with AI notes parsed."""
    views = [NoteView(created=log.createTime, created_by=log.createdBy, text=log.message or "",
                      ai=parse_message(log.message))
             for log in tools.iter_activity_logs(lead_id)]
    return sorted(views, key=lambda v: v.created or datetime.min.replace(tzinfo=PIXXI_TZ), reverse=True)


def has_layer1_note(notes: list[NoteView]) -> bool:
    return any(n.ai is not None and n.ai.agent_id == INTAKE_AGENT_ID for n in notes)


def new_in_last(leads: list[Lead], hours: int = 24, now: datetime | None = None) -> int:
    now = now or datetime.now(PIXXI_TZ)
    return sum(1 for l in leads if l.createTime and now - l.createTime <= timedelta(hours=hours))


def lead_row(lead: Lead, layer1: bool | None) -> dict[str, Any]:
    """One row of the leads table (no contact details or protected attributes)."""
    def money(v: float | None) -> str:
        if v is None:
            return "?"
        return f"{v / 1e6:.2f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"

    budget = "—" if lead.budget is None else f"{money(lead.budget_min)}–{money(lead.budget_max)}"
    return {
        "id": lead.id,
        "created": lead.createTime.strftime("%Y-%m-%d %H:%M") if lead.createTime else "",
        "source": lead.clientSource,
        "type": lead.clientType,
        "budget (AED)": budget,
        "bedrooms": (lead.rooms or "—").replace("|", "–"),
        "region": lead.region,
        "stage tag": lead.tagId,
        "agent": lead.agentInfo.name if lead.agentInfo else lead.agentId,
        "Layer 1 note": "—" if layer1 is None else ("✅" if layer1 else "⏳"),
    }


# -- middleware state (local, read-only) -----------------------------------------------------

def read_sqlite_rows(db_path: Path, table: str, limit: int = 50) -> list[dict]:
    """Newest rows of a middleware-store table, opened read-only. [] if the file/table is missing."""
    if not Path(db_path).exists():
        return []
    if table not in ("webhook_log", "write_ledger"):
        raise ValueError(f"unknown table {table}")
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            cols = "id, received_at, event, object_id, decision, reason" if table == "webhook_log" else "*"
            rows = conn.execute(f"SELECT {cols} FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def count_ai_notes_written(db_path: Path) -> int:
    if not Path(db_path).exists():
        return 0
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            return conn.execute("SELECT COUNT(*) FROM write_ledger WHERE kind = 'note'").fetchone()[0]
    except sqlite3.Error:
        return 0


def read_jsonl_tail(path: Path, limit: int = 50) -> list[dict]:
    """Last `limit` rows of a JSONL log, newest first. Skips unreadable lines."""
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[::-1]


def llm_usage(path: Path) -> dict[str, Any]:
    """Totals from eval/runs/llm_calls.jsonl (all runs logged on this machine)."""
    usage = {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
             "last_model": None, "last_prompt": None}
    if not Path(path).exists():
        return usage
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage["calls"] += 1
        usage["errors"] += 0 if r.get("ok") else 1
        usage["input_tokens"] += r.get("input_tokens") or 0
        usage["output_tokens"] += r.get("output_tokens") or 0
        usage["cost_usd"] += r.get("cost_usd") or 0
        usage["last_model"], usage["last_prompt"] = r.get("model"), r.get("prompt")
    usage["cost_usd"] = round(usage["cost_usd"], 6)
    return usage


# -- mock-only controls ------------------------------------------------------------------------

def load_arrivals(path: Path = SEED_ARRIVALS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def next_arrival(arrivals: list[dict], existing: list[Lead], rng: random.Random | None = None) -> dict:
    """The next seed arrival not yet in Pixxi; once all are used, a renamed copy without an id.

    Timestamps are dropped so the mock stamps the lead with the current time.
    """
    if not arrivals:
        raise ValueError("no seed arrivals found (run data/synthetic/generate_seed.py)")
    existing_ids = {l.id for l in existing}
    fresh = [a for a in arrivals if a["id"] not in existing_ids]
    if fresh:
        lead = copy.deepcopy(fresh[0])
    else:
        lead = copy.deepcopy((rng or random.Random()).choice(arrivals))
        del lead["id"]  # the mock assigns a new id
        n = 1 + sum(1 for l in existing if (l.name or "").startswith("Synthetic Lead X"))
        lead["name"] = f"Synthetic Lead X{n:02d}"
    for key in ("createTime", "updateTime"):
        lead.pop(key, None)
    return lead


class MockControl:
    """Calls to the mock's /mock/* endpoints (simulating humans). Refuses in real mode."""

    def __init__(self, settings: Settings, client: Any = None):
        self.settings = settings
        token = settings.pixxi_token.get_secret_value() if settings.pixxi_token else "mock-token"
        self.client = client or httpx.Client(base_url=settings.pixxi_base_url, timeout=30.0)
        self.headers = {"X-PIXXI-TOKEN": token}

    def _check(self) -> None:
        if self.settings.pixxi_mode != "mock":
            raise RuntimeError("simulation is only available against the mock (PIXXI_MODE=mock)")

    def new_lead(self, lead: dict) -> dict:
        self._check()
        r = self.client.post("/mock/leads", json=lead, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def human_note(self, lead_id: int, message: str, created_by: int | None = None) -> dict:
        self._check()
        body = {"message": message, **({"createdBy": created_by} if created_by else {})}
        r = self.client.post(f"/mock/leads/{lead_id}/notes", json=body, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def reset(self) -> None:
        self._check()
        self.client.post("/mock/reset", headers=self.headers).raise_for_status()


def service_up(url: str, timeout: float = 1.5) -> bool:
    """True if something answers at `url` (used for the mock and receiver health dots)."""
    try:
        return httpx.get(url, timeout=timeout).status_code < 500
    except httpx.HTTPError:
        return False
