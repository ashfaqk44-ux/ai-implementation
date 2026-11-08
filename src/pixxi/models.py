"""Pydantic models for Pixxi Data API payloads (docs/pixxi_openapi.yaml).

Field names follow Pixxi's camelCase. Every model keeps unknown fields (extra="allow")
so nothing returned by the API is lost, and is lenient where the real API is
inconsistent (IDs sent as strings or ints, "null" strings, "min|max" ranges).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

PIXXI_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# Pixxi timestamps have no timezone; assumed Gulf Standard Time (docs/api_findings.md).
PIXXI_TZ = ZoneInfo("Asia/Dubai")

# Lead attributes that must never be used for scoring (README hard rules).
SCORING_EXCLUDED_FIELDS = frozenset({"nationality", "gender", "birthdate", "name", "phone", "email"})


def parse_pixxi_time(value: Any) -> datetime | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=PIXXI_TZ)
    text = str(value).strip()
    try:
        return datetime.strptime(text, PIXXI_TIME_FORMAT).replace(tzinfo=PIXXI_TZ)
    except ValueError:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=PIXXI_TZ)


PixxiTime = Annotated[datetime | None, BeforeValidator(parse_pixxi_time)]


def format_pixxi_time(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(PIXXI_TZ)
    return value.strftime(PIXXI_TIME_FORMAT)


def parse_range(value: Any) -> tuple[str | None, str | None]:
    """Pixxi encodes ranges as "min|max" (e.g. budget "10000|100000", rooms "Studio|6")."""
    if value in (None, "", "null"):
        return None, None
    parts = str(value).split("|")
    low = parts[0] or None
    high = (parts[1] if len(parts) > 1 else parts[0]) or None
    return low, high


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _none_if_null(value: Any) -> Any:
    return None if value in ("null", "") else value


class PixxiModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class AgentInfo(PixxiModel):
    id: int | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class Lead(PixxiModel):
    id: int
    name: str | None = None
    clientType: str | None = None  # BUY | RENT
    status: str | None = None  # ACTIVE | INACTIVE | DEAL
    tagId: int | None = None  # lead stage
    agentId: int | None = None
    agentInfo: AgentInfo | None = None
    clientSource: str | None = None
    budget: str | None = None  # "min|max"
    rooms: str | None = None  # "min|max"
    size: str | None = None  # "min|max" sqft
    houseType: list[str] | None = None
    projectType: list[str] | None = None
    payMethod: str | None = None
    buyerType: str | None = None  # INVESTOR | OWNED
    region: str | None = None
    community: str | None = None
    regionId: int | None = None
    communityId: int | None = None
    notes: str | None = None
    interested_property: dict | None = Field(default=None, alias="property")
    reminderCount: int | None = None
    createTime: PixxiTime = None
    updateTime: PixxiTime = None
    # Present in the API but excluded from scoring (SCORING_EXCLUDED_FIELDS).
    nationality: str | None = None
    gender: str | None = None


    @field_validator("regionId", "communityId", "tagId", "agentId", mode="before")
    @classmethod
    def _ids(cls, v: Any) -> Any:
        return _none_if_null(v)

    @property
    def budget_min(self) -> float | None:
        return _to_float(parse_range(self.budget)[0])

    @property
    def budget_max(self) -> float | None:
        return _to_float(parse_range(self.budget)[1])

    @property
    def size_range(self) -> tuple[float | None, float | None]:
        low, high = parse_range(self.size)
        return _to_float(low), _to_float(high)

    @property
    def rooms_range(self) -> tuple[str | None, str | None]:
        return parse_range(self.rooms)

    def scoring_view(self) -> dict:
        """The lead without attributes that must never influence scoring."""
        return self.model_dump(exclude=set(SCORING_EXCLUDED_FIELDS))


class ActivityLog(PixxiModel):
    id: int | None = None
    leadId: int | None = None
    createdBy: int | None = None
    message: str | None = None
    type: str | None = None  # Note | Contact | Viewing | System
    properties: list[dict] | None = None
    extend: Any = None
    viewingStartTime: PixxiTime = None
    viewingEndTime: PixxiTime = None
    createTime: PixxiTime = None



class Listing(PixxiModel):
    id: int
    propertyId: str | None = None  # listing reference
    title: str | None = None
    listingType: str | None = None  # NEW | SELL | RENT
    propertyType: list[str] | None = None
    status: str | None = None
    price: float | None = None
    bedRooms: int | None = None  # -1 for multi-unit projects
    size: float | None = None
    region: str | None = None
    regionId: int | None = None
    cityId: int | None = None
    cityName: str | None = None
    community: str | None = None
    communityId: int | None = None
    developer: str | None = None
    developerId: int | None = None
    createTime: PixxiTime = None
    updateTime: PixxiTime = None


    @field_validator("regionId", "communityId", "developerId", "cityId", mode="before")
    @classmethod
    def _ids(cls, v: Any) -> Any:
        return _none_if_null(v)

    @field_validator("propertyType", mode="before")
    @classmethod
    def _as_list(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


class Agent(PixxiModel):
    """Shape of GET /agent/list items is undocumented; modelled on agentInfo."""

    id: int
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class Reminder(PixxiModel):
    id: int | None = None
    title: str | None = None
    description: str | None = None
    reminderType: str | None = None
    status: str | None = None
    object_type: str | None = None
    object_id: int | None = None
    object_module: str | None = None
    participants: list[int] | None = None
    reminder_start_time: PixxiTime = None
    reminder_end_time: PixxiTime = None



class MatchResult(PixxiModel):
    propertyId: int
    leadsId: int
    propertyReference: str | None = None
    matchRate: float | None = None
    matchDetails: dict | None = None
    lastMatchTime: PixxiTime = None



T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Pixxi pages come in two shapes; both are normalised here.

    lead/list:      {list, total, pages, current}
    activity-logs:  {list, currentPage, totalSize, totalPages, offset}
    """

    items: list[T]
    page: int
    total: int | None = None
    pages: int | None = None

    @property
    def has_next(self) -> bool:
        return self.pages is not None and self.page < self.pages


def to_page(data: Any, model: type[BaseModel], size: int | None = None) -> Page:
    data = data or {}
    if isinstance(data, list):
        return Page(items=[model.model_validate(x) for x in data], page=1, total=len(data), pages=1)
    items = [model.model_validate(x) for x in data.get("list") or []]
    page = data.get("current") or data.get("currentPage") or 1
    total = data.get("total", data.get("totalSize"))
    pages = data.get("pages", data.get("totalPages"))
    if pages is None and total is not None and size:
        # Listings pages carry totalSize but no page count.
        pages = max(1, -(-int(total) // int(size)))
    return Page(items=items, page=int(page), total=total, pages=pages)
