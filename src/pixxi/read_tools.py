"""C6 Read tools: read-only access to Pixxi.

Agents choose which of these to call (perception is part of autonomy). Most Pixxi reads
are POST requests with a JSON filter body, but none of them change data. Every path used
here is on READ_PATHS; a test asserts nothing else is ever called through this module.
Writes go only through C8 (write_client.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterator, Literal

from src.config import Settings, get_settings
from src.pixxi.http import PixxiError, PixxiHttp
from src.pixxi.models import (
    ActivityLog,
    Agent,
    Lead,
    Listing,
    MatchResult,
    Page,
    Reminder,
    format_pixxi_time,
    to_page,
)

__all__ = ["ReadTools", "PixxiError", "READ_PATHS"]

READ_PATHS = frozenset(
    {
        ("POST", "/pixxiapi/v1/lead/list"),
        ("POST", "/pixxiapi/v1/lead/activity-logs"),
        ("POST", "/pixxiapi/v1/properties"),
        ("GET", "/pixxiapi/v1/{propertyId}"),
        ("GET", "/pixxiapi/v1/agent/list"),
        ("POST", "/pixxiapi/v1/reminder/list"),
        ("POST", "/pixxiapi/v1/matching/listings/passive/page"),
        ("GET", "/pixxiapi/webhook/v1/subscription/list"),
    }
)

LeadSort = Literal["ID", "CREATE_TIME", "UPDATE_TIME"]
SortType = Literal["ASC", "DESC"]


def _clean(body: dict) -> dict:
    return {k: v for k, v in body.items() if v is not None}


def _time(value: datetime | None) -> str | None:
    return format_pixxi_time(value) if value is not None else None


class ReadTools:
    def __init__(self, settings: Settings | None = None, http: PixxiHttp | None = None, client=None):
        self.settings = settings or get_settings()
        self.http = http or PixxiHttp(self.settings, client=client)

    # -- leads ---------------------------------------------------------------
    def list_leads(
        self,
        page: int = 1,
        size: int | None = None,
        sort: LeadSort = "ID",
        sort_type: SortType = "DESC",
        status: Literal["ACTIVE", "INACTIVE", "DEAL"] | None = None,
        client_type: Literal["BUY", "RENT"] | None = None,
        search: str | None = None,
    ) -> Page[Lead]:
        """POST /pixxiapi/v1/lead/list"""
        body = _clean(
            {
                "page": page,
                "size": size or self.settings.pixxi_page_size,
                "sort": sort,
                "sortType": sort_type,
                "status": status,
                "clientType": client_type,
                "searchKeywords": search,
            }
        )
        return to_page(self.http.request("POST", "/pixxiapi/v1/lead/list", kind="read", json=body), Lead)

    def iter_leads(self, **filters) -> Iterator[Lead]:
        """All leads matching the filters, following pagination."""
        page = 1
        while True:
            result = self.list_leads(page=page, **filters)
            yield from result.items
            if not result.has_next or not result.items:
                return
            page += 1

    def find_lead(self, lead_id: int) -> Lead | None:
        """There is no single-lead endpoint; searchKeywords matches on ID."""
        for lead in self.list_leads(search=str(lead_id), size=20).items:
            if lead.id == int(lead_id):
                return lead
        return None

    # -- activity logs ---------------------------------------------------------
    def list_activity_logs(
        self,
        lead_id: int,
        log_type: Literal["Note", "Contact", "Viewing", "System"] | None = None,
        date_start: datetime | None = None,
        date_end: datetime | None = None,
        page: int = 1,
        size: int | None = None,
    ) -> Page[ActivityLog]:
        """POST /pixxiapi/v1/lead/activity-logs"""
        body = _clean(
            {
                "leadId": int(lead_id),
                "type": log_type,
                "dateStart": _time(date_start),
                "dateEnd": _time(date_end),
                "page": page,
                "size": size or self.settings.pixxi_page_size,
            }
        )
        data = self.http.request("POST", "/pixxiapi/v1/lead/activity-logs", kind="read", json=body)
        return to_page(data, ActivityLog)

    def iter_activity_logs(self, lead_id: int, **filters) -> Iterator[ActivityLog]:
        page = 1
        while True:
            result = self.list_activity_logs(lead_id, page=page, **filters)
            yield from result.items
            if not result.has_next or not result.items:
                return
            page += 1

    # -- listings ------------------------------------------------------------
    def list_listings(
        self,
        page: int = 1,
        size: int | None = None,
        listing_type: Literal["NEW", "SELL", "RENT"] | None = None,
        property_types: list[str] | None = None,
        start_price: float | None = None,
        end_price: float | None = None,
        bedrooms: list[int] | None = None,
        region_ids: list[int] | None = None,
        community_ids: list[int] | None = None,
        completion_status: list[str] | None = None,
        name: str | None = None,
    ) -> Page[Listing]:
        """POST /pixxiapi/v1/properties (website listings)"""
        body = _clean(
            {
                "page": page,
                "size": size or self.settings.pixxi_page_size,
                "listingType": listing_type,
                "propertyType": property_types,
                "startPrice": start_price,
                "endPrice": end_price,
                "bedRoomNum": bedrooms,
                "regionIds": region_ids,
                "communityIds": community_ids,
                "completionStatusList": completion_status,
                "name": name,
            }
        )
        data = self.http.request("POST", "/pixxiapi/v1/properties", kind="read", json=body)
        return to_page(data, Listing, size=body["size"])

    def get_listing(self, property_id: int | str) -> Listing:
        """GET /pixxiapi/v1/{propertyId}"""
        data = self.http.request("GET", f"/pixxiapi/v1/{property_id}", kind="read")
        return Listing.model_validate(data)

    # -- agents, reminders, matches -------------------------------------------
    def list_agents(self) -> list[Agent]:
        """GET /pixxiapi/v1/agent/list (response shape undocumented)"""
        data = self.http.request("GET", "/pixxiapi/v1/agent/list", kind="read")
        items = data.get("list", []) if isinstance(data, dict) else (data or [])
        return [Agent.model_validate(a) for a in items]

    def list_reminders(self, page: int = 1, size: int | None = None) -> Page[Reminder]:
        """POST /pixxiapi/v1/reminder/list (read only; the middleware never edits reminders)"""
        body = {"page": page, "size": size or self.settings.pixxi_page_size}
        data = self.http.request("POST", "/pixxiapi/v1/reminder/list", kind="read", json=body)
        return to_page(data, Reminder)

    def get_matches(
        self,
        lead_ids: list[int] | None = None,
        property_ids: list[int] | None = None,
        agent_ids: list[int] | None = None,
        page: int = 1,
        size: int | None = None,
    ) -> Page[MatchResult]:
        """POST /pixxiapi/v1/matching/listings/passive/page (Pixxi's own matching; baseline for Agent 5)"""
        body = _clean(
            {
                "leadsIds": lead_ids,
                "propertyIds": property_ids,
                "agentIds": agent_ids,
                "page": page,
                "size": size or self.settings.pixxi_page_size,
            }
        )
        data = self.http.request(
            "POST", "/pixxiapi/v1/matching/listings/passive/page", kind="read", json=body
        )
        return to_page(data, MatchResult)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "ReadTools":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
