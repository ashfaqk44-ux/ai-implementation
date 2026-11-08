"""C8 Write client: the single exit point from the middleware to Pixxi.

Exactly two operations, both POST:
- add_note        -> POST /pixxiapi/v1/lead/addLog/{AppName}
- create_reminder -> POST /pixxiapi/v1/reminder

By design (docs/decisions.md) the client has no way to call PUT /lead/update (which could
overwrite fields or reassign a lead), PUT /reminder or DELETE /reminder/{id}, even though
the Pixxi API offers them. Every write is AI-tagged, recorded in the write ledger (used by
the webhook receiver's loop guard) and appended to a local JSONL audit log.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.base import ReminderRequest
from src.audit.note import AgentNote
from src.audit.store import MiddlewareStore
from src.config import Settings, get_settings
from src.pixxi.http import PixxiHttp
from src.pixxi.models import format_pixxi_time

WRITE_PATHS = frozenset(
    {
        ("POST", "/pixxiapi/v1/lead/addLog/{AppName}"),
        ("POST", "/pixxiapi/v1/reminder"),
    }
)


class UntaggedWriteError(ValueError):
    """Raised when something tries to write without the AI tag."""


class WriteClient:
    def __init__(
        self,
        store: MiddlewareStore,
        settings: Settings | None = None,
        http: PixxiHttp | None = None,
        client=None,
        audit_log: Path | None = None,
    ):
        self.settings = settings or get_settings()
        self.http = http or PixxiHttp(self.settings, client=client)
        self.store = store
        self.audit_log = audit_log if audit_log is not None else self.settings.audit_log

    def add_note(self, note: AgentNote) -> None:
        if not isinstance(note, AgentNote) or note.ai_generated is not True:
            raise UntaggedWriteError("Only AI-tagged AgentNote objects may be written")
        body = {"leadId": note.lead_id, "message": note.to_message()}
        # Record before sending: a webhook caused by this write can arrive before the response.
        self.store.record_write(note.lead_id, "note", note.agent_id, note.run_id)
        self.http.request(
            "POST", f"/pixxiapi/v1/lead/addLog/{self.settings.pixxi_app_name}", kind="write", json=body
        )
        self._audit("note", body, note.agent_id, note.run_id)

    def create_reminder(self, req: ReminderRequest) -> None:
        if not isinstance(req, ReminderRequest):
            raise UntaggedWriteError("Only ReminderRequest objects may be written")
        body = {
            "participants": req.participants,
            "reminderType": "TASK",
            "title": req.marked_title,
            "description": req.as_note().to_message(),
            "reminder_start_time": format_pixxi_time(req.start_time),
            "send_notification_before": req.notify_before_minutes,
            "object_type": "LEADS_REMINDER",
            "object_id": req.lead_id,
            "object_module": "Buy Lead" if req.client_type == "BUY" else "Rent Lead",
            "status": "INIT",
            "operationPermission": "COMPLETE_ONLY",
        }
        body = {k: v for k, v in body.items() if v is not None}
        self.store.record_write(req.lead_id, "reminder", req.agent_id, req.run_id)
        self.http.request("POST", "/pixxiapi/v1/reminder", kind="write", json=body)
        self._audit("reminder", body, req.agent_id, req.run_id)

    def _audit(self, kind: str, body: dict, agent_id: str, run_id: str) -> None:
        if self.audit_log is None:
            return
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        row = {"kind": kind, "agent_id": agent_id, "run_id": run_id, "body": body}
        with self.audit_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self.http.close()
