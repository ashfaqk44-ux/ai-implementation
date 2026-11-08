"""Mock Pixxi Data API, mirroring the official spec (docs/pixxi_openapi.yaml).

Kept outside src/ on purpose (README section 10): the middleware must work against this
mock or the real API through configuration alone. Same paths, methods, X-PIXXI-TOKEN
header, {statusCode, message, data} envelope, field names and "yyyy-MM-dd HH:mm:ss"
timestamps as the real API. Behaviour the docs leave open is modelled on the worst case
and listed in docs/api_findings.md:

- POST /lead/addLog fires ADD_LEAD_NOTE *and* UPDATE_LEADS, and bumps the lead's updateTime.
- POST /reminder (LEADS_REMINDER) fires ADD_REMINDER *and* UPDATE_LEADS, and bumps
  reminderCount and updateTime.
- Missing token -> 401. Optional rate limiting (>= 2 s between reads, 2,000/hour) -> 429.

PUT /lead/update, PUT /reminder and DELETE /reminder/{id} exist (they do in the real API),
but every call to them is recorded in `forbidden_calls` so tests can prove the middleware
never uses them.

/mock/* endpoints are NOT part of Pixxi. They simulate humans (a new lead arriving, a real
estate agent logging a note) and let tests inspect or reset the mock.

Run: uvicorn mock_pixxi.app:app --port 8001
Seed dataset instead of the default fixtures: set MOCK_FIXTURES_DIR=data/synthetic/seed
(any of leads.json, listings.json, agents.json, activity_logs.json found there replaces the default).
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

# Declared so the /docs page shows an "Authorize" button for the token. The actual check
# happens in the auth middleware below, which applies to every /pixxiapi route.
TOKEN_SCHEME = APIKeyHeader(
    name="X-PIXXI-TOKEN",
    auto_error=False,
    description="Any non-empty value works on the mock (e.g. mock-token). The real API needs your Pixxi token.",
)

log = logging.getLogger("mock_pixxi")

FIXTURES = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Asia/Dubai")
API_USER_ID = 1  # createdBy for records written through the API (unverified in the real API)
EVENTS = [
    "ADD_LEADS", "UPDATE_LEADS", "ADD_LEAD_NOTE", "ADD_REMINDER", "ADD_LISTINGS",
    "UPDATE_LISTINGS", "DELETE_LISTINGS", "ADD_NOTIFICATIONS", "ADD_PORTAL_LEAD", "UPDATE_PORTAL_LEAD",
]
MAX_PAGE_SIZE = 500

Dispatcher = Callable[[str, dict], None]


def now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def ok(data: Any) -> dict:
    return {"statusCode": 200, "message": "success", "data": data}


def fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"statusCode": status, "message": message, "data": None})


def http_dispatcher(url: str, payload: dict) -> None:
    try:
        httpx.post(url, json=payload, timeout=5.0)
    except httpx.HTTPError as exc:  # a real webhook sender does not crash on a dead receiver
        log.warning("webhook delivery to %s failed: %s", url, exc)


def _load(name: str, fixtures_dir: Path | None = None) -> list[dict]:
    """Load a fixture file, preferring the dataset folder when it has one."""
    for folder in (fixtures_dir, FIXTURES):
        if folder is not None and (folder / name).exists():
            return json.loads((folder / name).read_text(encoding="utf-8"))
    return []


def _range(value: Any) -> tuple[float | None, float | None]:
    if not value:
        return None, None
    parts = str(value).split("|")
    def num(x: str) -> float | None:
        try:
            return float(x)
        except ValueError:
            return None
    return num(parts[0]), num(parts[-1])


def _paginate(items: list, page: int, size: int) -> tuple[list, int]:
    size = max(1, min(int(size or 10), MAX_PAGE_SIZE))
    page = max(1, int(page or 1))
    pages = max(1, math.ceil(len(items) / size))
    return items[(page - 1) * size: page * size], pages


class MockState:
    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self.fixtures_dir = fixtures_dir
        self.reset()

    def reset(self) -> None:
        d = self.fixtures_dir
        self.leads: dict[int, dict] = {l["id"]: l for l in _load("leads.json", d)}
        self.listings: dict[int, dict] = {p["id"]: p for p in _load("listings.json", d)}
        self.agents: list[dict] = _load("agents.json", d)
        # A dataset folder without activity logs starts with none (not the default fixture logs).
        self.logs: list[dict] = _load("activity_logs.json", d) if d is None or (d / "activity_logs.json").exists() else []
        self.reminders: list[dict] = []
        self.subscriptions: list[dict] = []
        self.forbidden_calls: list[dict] = []
        self.webhooks_fired: list[dict] = []
        self._next_id = 990000

    def next_id(self) -> int:
        self._next_id += 1
        return self._next_id


def create_app(
    dispatcher: Dispatcher | None = None,
    enforce_rate_limit: bool | None = None,
    fixtures_dir: Path | str | None = None,
) -> FastAPI:
    if fixtures_dir is None and os.getenv("MOCK_FIXTURES_DIR"):
        fixtures_dir = os.environ["MOCK_FIXTURES_DIR"]
    if enforce_rate_limit is None:
        enforce_rate_limit = os.getenv("MOCK_ENFORCE_RATE_LIMIT", "").lower() in ("1", "true", "yes")
    app = FastAPI(
        title="Mock Pixxi Data API",
        description="Mirrors the official Pixxi Data API for development. Click **Authorize** and "
        "enter any token (e.g. `mock-token`) before trying `/pixxiapi` endpoints. "
        "`/mock/*` endpoints are not part of Pixxi; they simulate humans.",
        dependencies=[Security(TOKEN_SCHEME)],
    )
    state = MockState(Path(fixtures_dir) if fixtures_dir is not None else None)
    app.state.mock = state
    app.state.dispatcher = dispatcher or http_dispatcher
    read_times: dict[str, deque] = {}

    # -- auth + rate limit ---------------------------------------------------
    WRITE_PATH_MARKERS = ("/addLog/", "/lead/update/", "/webhook/v1/form")

    @app.middleware("http")
    async def auth_and_rate_limit(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/pixxiapi") and not path.startswith("/integration"):
            return await call_next(request)
        token = request.headers.get("X-PIXXI-TOKEN")
        if not token:
            return fail(401, "missing X-PIXXI-TOKEN")
        is_write = request.method in ("PUT", "DELETE") or any(m in path for m in WRITE_PATH_MARKERS) or (
            request.method == "POST" and path == "/pixxiapi/v1/reminder"
        )
        if enforce_rate_limit and not is_write:
            now = time.monotonic()
            times = read_times.setdefault(token, deque())
            while times and now - times[0] > 3600:
                times.popleft()
            if (times and now - times[-1] < 2.0) or len(times) >= 2000:
                return fail(429, "Too Many Requests")
            times.append(now)
        return await call_next(request)

    # -- webhook firing ---------------------------------------------------------
    def fire(background: BackgroundTasks, event: str, payload: dict) -> None:
        for sub in state.subscriptions:
            if sub["event"] == event and sub["status"] == "ACTIVE":
                body = copy.deepcopy(payload)
                state.webhooks_fired.append({"event": event, "callbackUrl": sub["callbackUrl"], "id": body.get("id")})
                background.add_task(app.state.dispatcher, sub["callbackUrl"], body)

    def touch_lead(lead: dict) -> None:
        lead["updateTime"] = now_str()
        lead["updateBy"] = API_USER_ID

    # ===================== Leads =====================
    @app.post("/pixxiapi/v1/lead/list")
    async def lead_list(request: Request):
        body = await _json(request)
        items = list(state.leads.values())
        if body.get("status"):
            items = [l for l in items if l.get("status") == body["status"]]
        if body.get("clientType"):
            items = [l for l in items if l.get("clientType") == body["clientType"]]
        if body.get("searchKeywords"):
            kw = str(body["searchKeywords"]).lower()
            items = [l for l in items if kw == str(l["id"]) or kw in (l.get("name") or "").lower()
                     or kw in (l.get("phone") or "")]
        sort_key = {"ID": "id", "CREATE_TIME": "createTime", "UPDATE_TIME": "updateTime"}.get(body.get("sort") or "ID", "id")
        items.sort(key=lambda l: l.get(sort_key) or "", reverse=(body.get("sortType") or "DESC") == "DESC")
        page_items, pages = _paginate(items, body.get("page", 1), body.get("size", 10))
        return ok({"list": page_items, "total": len(items), "pages": pages, "current": int(body.get("page") or 1)})

    @app.post("/pixxiapi/v1/lead/activity-logs")
    async def lead_activity_logs(request: Request):
        body = await _json(request)
        if body.get("leadId") is None:
            return fail(400, "leadId is required")
        lead_id = int(body["leadId"])
        items = [l for l in state.logs if l["leadId"] == lead_id]
        if body.get("type"):
            items = [l for l in items if l["type"] == body["type"]]
        if body.get("dateStart"):
            items = [l for l in items if l["createTime"] >= body["dateStart"]]
        if body.get("dateEnd"):
            items = [l for l in items if l["createTime"] <= body["dateEnd"]]
        items.sort(key=lambda l: l["createTime"], reverse=True)
        size = int(body.get("size") or 10)
        page_items, pages = _paginate(items, body.get("page", 1), size)
        return ok({"currentPage": int(body.get("page") or 1), "offset": size, "totalSize": len(items),
                   "totalPages": pages, "list": page_items})

    @app.post("/pixxiapi/v1/lead/addLog/{AppName}")
    async def lead_add_log(AppName: str, request: Request, background: BackgroundTasks):
        body = await _json(request)
        if body.get("leadId") is None:
            return fail(400, "leadId is required")
        lead = state.leads.get(int(body["leadId"]))
        if lead is None:
            return fail(400, "lead not found")
        entry = {
            "id": state.next_id(), "leadId": lead["id"], "createdBy": API_USER_ID,
            "message": body.get("message"), "type": "Note", "properties": [],
            "extend": json.dumps({"appName": AppName}), "portal": None,
            "viewingStartTime": None, "viewingEndTime": None, "createTime": now_str(),
        }
        if body.get("callRecordingUrl"):
            entry["callRecordingUrl"] = body["callRecordingUrl"]
        state.logs.append(entry)
        touch_lead(lead)
        fire(background, "ADD_LEAD_NOTE", entry)
        fire(background, "UPDATE_LEADS", lead)
        return ok(None)

    @app.put("/pixxiapi/v1/lead/update/{AppName}")
    async def lead_update(AppName: str, request: Request, background: BackgroundTasks):
        body = await _json(request)
        state.forbidden_calls.append({"method": "PUT", "path": f"/pixxiapi/v1/lead/update/{AppName}", "body": body})
        lead = state.leads.get(int(body.get("id") or 0))
        if lead is None:
            return fail(400, "lead not found")
        lead.update({k: v for k, v in body.items() if k != "id"})
        touch_lead(lead)
        fire(background, "UPDATE_LEADS", lead)
        return ok(None)

    @app.get("/pixxiapi/v1/lead/custom/fields")
    def lead_custom_fields(module: str):
        return ok([])

    # ===================== Listings =====================
    @app.post("/pixxiapi/v1/properties")
    async def properties(request: Request):
        body = await _json(request)
        items = list(state.listings.values())
        if body.get("listingType"):
            items = [p for p in items if p.get("listingType") == body["listingType"]]
        if body.get("propertyType"):
            wanted = set(body["propertyType"])
            items = [p for p in items if wanted & set(p.get("propertyType") or [])]
        if body.get("startPrice") is not None:
            items = [p for p in items if (p.get("price") or 0) >= body["startPrice"]]
        if body.get("endPrice") is not None:
            items = [p for p in items if (p.get("price") or 0) <= body["endPrice"]]
        if body.get("bedRoomNum"):
            items = [p for p in items if p.get("bedRooms") in body["bedRoomNum"]]
        if body.get("regionIds"):
            items = [p for p in items if int(p.get("regionId") or 0) in body["regionIds"]]
        if body.get("communityIds"):
            items = [p for p in items if str(p.get("communityId")) != "null"
                     and int(p.get("communityId") or 0) in body["communityIds"]]
        if body.get("name"):
            items = [p for p in items if body["name"].lower() in (p.get("title") or "").lower()]
        items.sort(key=lambda p: p["id"], reverse=(body.get("sortType") or "DESC") == "DESC")
        page_items, _ = _paginate(items, body.get("page", 1), body.get("size", 10))
        return ok({"totalListings": len(items), "currentPage": int(body.get("page") or 1),
                   "totalSize": len(items), "list": page_items})

    # ===================== Agents =====================
    @app.get("/pixxiapi/v1/agent/list")
    def agent_list():
        return ok(state.agents)

    # ===================== Reminders =====================
    @app.post("/pixxiapi/v1/reminder/list")
    async def reminder_list(request: Request):
        body = await _json(request)
        items = sorted(state.reminders, key=lambda r: r["id"], reverse=True)
        page_items, pages = _paginate(items, body.get("page", 1), body.get("size", 10))
        return ok({"list": page_items, "total": len(items), "pages": pages, "current": int(body.get("page") or 1)})

    @app.post("/pixxiapi/v1/reminder")
    async def reminder_create(request: Request, background: BackgroundTasks):
        body = await _json(request)
        missing = [f for f in ("participants", "reminderType", "title", "reminder_start_time") if not body.get(f)]
        if missing:
            return fail(400, f"missing required fields: {', '.join(missing)}")
        if body["reminderType"] in ("EVENT", "VIEWING") and not body.get("reminder_end_time"):
            return fail(400, "reminder_end_time is required for EVENT and VIEWING")
        lead = None
        if body.get("object_type") == "LEADS_REMINDER":
            lead = state.leads.get(int(body.get("object_id") or 0))
            if lead is None:
                return fail(400, "lead not found")
        stamp = now_str()
        reminder = {
            "id": state.next_id(), "title": body["title"], "description": body.get("description"),
            "createUser": {"id": API_USER_ID, "name": "API"},
            "client": {k: lead.get(k) for k in ("id", "name", "phone", "email")} if lead else None,
            "clientId": lead["id"] if lead else None,
            "object_type": body.get("object_type", "CALENDAR_REMINDER"),
            "object_id": body.get("object_id"), "object_module": body.get("object_module"),
            "object_data": copy.deepcopy(lead) if lead else None,
            "participants": body["participants"],
            "reminder_start_time": body["reminder_start_time"],
            "reminder_end_time": body.get("reminder_end_time"),
            "send_notification_before": body.get("send_notification_before"),
            "properties": body.get("properties", []), "status": body.get("status", "INIT"),
            "reminderType": body["reminderType"],
            "operationPermission": body.get("operationPermission"),
            "message": None, "create_time": stamp, "update_time": stamp,
        }
        state.reminders.append(reminder)
        fire(background, "ADD_REMINDER", reminder)
        if lead is not None:
            lead["reminderCount"] = (lead.get("reminderCount") or 0) + 1
            touch_lead(lead)
            fire(background, "UPDATE_LEADS", lead)
        return ok(None)

    @app.put("/pixxiapi/v1/reminder")
    async def reminder_update(request: Request):
        body = await _json(request)
        state.forbidden_calls.append({"method": "PUT", "path": "/pixxiapi/v1/reminder", "body": body})
        for r in state.reminders:
            if r["id"] == body.get("id"):
                r.update(body)
                r["update_time"] = now_str()
                return ok(None)
        return fail(400, "reminder not found")

    @app.delete("/pixxiapi/v1/reminder/{id}")
    def reminder_delete(id: int):
        state.forbidden_calls.append({"method": "DELETE", "path": f"/pixxiapi/v1/reminder/{id}", "body": None})
        state.reminders = [r for r in state.reminders if r["id"] != id]
        return ok(None)

    # ===================== Matches =====================
    @app.post("/pixxiapi/v1/matching/listings/passive/page")
    async def matches(request: Request):
        body = await _json(request)
        leads = [state.leads[i] for i in body.get("leadsIds") or state.leads.keys() if i in state.leads]
        listings = [state.listings[i] for i in body.get("propertyIds") or state.listings.keys() if i in state.listings]
        if body.get("agentIds"):
            leads = [l for l in leads if l.get("agentId") in body["agentIds"]]
        results = [r for l in leads for p in listings if (r := _match(l, p))["matchRate"] > 0]
        results.sort(key=lambda r: r["matchRate"], reverse=True)
        size = int(body.get("size") or 10)
        page_items, pages = _paginate(results, body.get("page", 1), size)
        return ok({"currentPage": int(body.get("page") or 1), "offset": size, "totalSize": len(results),
                   "totalPages": pages, "list": page_items})

    # ===================== Webhooks API =====================
    @app.post("/pixxiapi/webhook/v1/subscribe")
    async def subscribe(request: Request):
        body = await _json(request)
        if body.get("event") not in EVENTS or not body.get("callbackUrl"):
            return fail(400, "event and callbackUrl are required")
        if not any(s["event"] == body["event"] and s["callbackUrl"] == body["callbackUrl"] for s in state.subscriptions):
            stamp = now_str()
            state.subscriptions.append({"id": len(state.subscriptions) + 1, "tenantId": 1, "event": body["event"],
                                        "callbackUrl": body["callbackUrl"], "status": "ACTIVE",
                                        "createTime": stamp, "updateTime": stamp})
        return ok(True)

    @app.post("/pixxiapi/webhook/v1/unsubscribe")
    async def unsubscribe(request: Request):
        body = await _json(request)
        if body.get("event") not in EVENTS or not body.get("url"):
            return fail(400, "event and url are required")
        state.subscriptions = [s for s in state.subscriptions
                               if not (s["event"] == body["event"] and s["callbackUrl"] == body["url"])]
        return ok(True)

    @app.get("/pixxiapi/webhook/v1/subscription/list")
    def subscription_list(event: str | None = None):
        return ok([s for s in state.subscriptions if event is None or s["event"] == event])

    # ===================== Mock-only controls (not Pixxi) =====================
    @app.post("/mock/leads")
    async def mock_new_lead(request: Request, background: BackgroundTasks):
        """Simulate a new lead arriving in Pixxi (portal, form, manual entry)."""
        body = await _json(request)
        stamp = now_str()
        lead = {"id": state.next_id(), "createTime": stamp, "updateTime": stamp, "status": "ACTIVE",
                "clientType": "BUY", "reminderCount": 0, **body}
        state.leads[lead["id"]] = lead
        fire(background, "ADD_LEADS", lead)
        return lead

    @app.post("/mock/leads/{lead_id}/notes")
    async def mock_human_note(lead_id: int, request: Request, background: BackgroundTasks):
        """Simulate a real estate agent logging a note in the Pixxi UI."""
        lead = state.leads.get(lead_id)
        if lead is None:
            return fail(404, "lead not found")
        body = await _json(request)
        entry = {"id": state.next_id(), "leadId": lead_id, "createdBy": body.get("createdBy", lead.get("agentId")),
                 "message": body.get("message", ""), "type": body.get("type", "Note"), "properties": [],
                 "extend": None, "portal": None, "viewingStartTime": None, "viewingEndTime": None,
                 "createTime": now_str()}
        state.logs.append(entry)
        touch_lead(lead)
        lead["updateBy"] = entry["createdBy"]
        fire(background, "ADD_LEAD_NOTE", entry)
        fire(background, "UPDATE_LEADS", lead)
        return entry

    @app.get("/mock/state")
    def mock_state():
        return {"leads": len(state.leads), "logs": len(state.logs), "reminders": state.reminders,
                "subscriptions": state.subscriptions, "forbidden_calls": state.forbidden_calls,
                "webhooks_fired": state.webhooks_fired}

    @app.post("/mock/reset")
    def mock_reset():
        state.reset()
        read_times.clear()
        return {"status": "reset"}

    # Registered last: a one-segment catch-all would otherwise shadow nothing else, but keep it safe.
    @app.get("/pixxiapi/v1/{propertyId}")
    def single_listing(propertyId: str):
        for p in state.listings.values():
            if str(p["id"]) == propertyId or p.get("propertyId") == propertyId:
                return ok(p)
        return fail(404, "Property not found")

    return app


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _match(lead: dict, listing: dict) -> dict:
    """A simple stand-in for Pixxi's matching engine (the real algorithm is not documented)."""
    fields = []
    low, high = _range(lead.get("budget"))
    price = listing.get("price") or 0
    fields.append(("BUDGET", "Budget", 40, low is not None and high is not None and low <= price <= high))
    fields.append(("LOCATION", "Location", 30, str(lead.get("regionId")) == str(listing.get("regionId"))))
    rlow, rhigh = _range(str(lead.get("rooms") or "").replace("Studio", "0"))
    beds = listing.get("bedRooms")
    fields.append(("ROOMS", "Bedrooms", 30, rlow is not None and beds is not None and rlow <= beds <= (rhigh or rlow)))
    rate = sum(total for _, _, total, hit in fields if hit)
    return {
        "propertyId": listing["id"], "propertyReference": listing.get("propertyId"), "leadsId": lead["id"],
        "matchRate": float(rate),
        "matchDetails": {"propertyType": f"RESIDENTIAL_{listing.get('listingType', 'SELL')}",
                         "matchFields": [{"totalRate": total, "matchLabel": label, "matchField": key,
                                          "isRequired": key == "BUDGET", "matchRate": total if hit else 0}
                                         for key, label, total, hit in fields]},
        "lastMatchTime": now_str(),
    }


app = create_app()
