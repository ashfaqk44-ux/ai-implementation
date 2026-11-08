import re

import pytest
from fastapi.testclient import TestClient

from mock_pixxi.app import create_app
from src.config import Settings
from src.pixxi.http import PixxiError
from src.pixxi.models import Lead
from src.pixxi.read_tools import READ_PATHS, ReadTools


def _templated(path: str) -> str:
    return re.sub(r"^/pixxiapi/v1/(\d+)$", "/pixxiapi/v1/{propertyId}", path)


def test_list_leads_returns_typed_page(read_tools):
    page = read_tools.list_leads()
    assert page.total == 5
    assert all(isinstance(lead, Lead) for lead in page.items)
    assert page.items[0].id == 900005  # default sort ID DESC


def test_iter_leads_follows_pagination(read_tools):
    leads = list(read_tools.iter_leads(size=2))
    assert len(leads) == 5
    assert sum(1 for c in read_tools.http.calls if c[1] == "/pixxiapi/v1/lead/list") == 3


def test_lead_filters(read_tools):
    assert [l.id for l in read_tools.list_leads(status="DEAL").items] == [900005]
    assert [l.id for l in read_tools.list_leads(client_type="RENT").items] == [900004]
    oldest_update = read_tools.list_leads(sort="UPDATE_TIME", sort_type="ASC").items[0]
    assert oldest_update.id == 900003  # the dormant lead


def test_find_lead(read_tools):
    assert read_tools.find_lead(900002).name == "Synthetic Lead 002"
    assert read_tools.find_lead(123) is None


def test_activity_logs(read_tools):
    logs = read_tools.list_activity_logs(900002).items
    assert len(logs) == 1 and logs[0].type == "Note"
    assert read_tools.list_activity_logs(900002, log_type="Contact").items == []


def test_listings_and_single_listing(read_tools):
    page = read_tools.list_listings(listing_type="SELL", end_price=1_000_000)
    assert [p.id for p in page.items] == [351003]
    assert read_tools.get_listing(351002).title.startswith("Synthetic 4BR")
    with pytest.raises(PixxiError) as exc:
        read_tools.get_listing(999)
    assert exc.value.status_code == 404


def test_listing_pagination_without_page_count(read_tools):
    page = read_tools.list_listings(size=3)
    assert page.total == 4 and page.pages == 2 and page.has_next


def test_agents_and_matches(read_tools):
    assert {a.id for a in read_tools.list_agents()} == {2031, 2032, 2033}
    matches = read_tools.get_matches(lead_ids=[900003]).items
    assert matches[0].propertyId == 351003 and matches[0].matchRate == 100


def test_only_read_paths_are_called(read_tools):
    read_tools.list_leads(); read_tools.find_lead(900001); read_tools.list_activity_logs(900001)
    read_tools.list_listings(); read_tools.get_listing(351001); read_tools.list_agents()
    read_tools.list_reminders(); read_tools.get_matches()
    assert {(m, _templated(p)) for m, p in read_tools.http.calls} <= READ_PATHS


def test_read_tools_expose_no_write_methods():
    forbidden = ("put", "patch", "post", "delete", "update", "create", "add", "write", "assign")
    public = [n.lower() for n in dir(ReadTools) if not n.startswith("_")]
    assert not [n for n in public if any(w in n for w in forbidden)]


def test_missing_token_is_rejected():
    settings = Settings(_env_file=None, pixxi_token=None, pixxi_min_read_interval=0)
    tools = ReadTools(settings=settings, client=TestClient(create_app(dispatcher=lambda u, p: None)))
    with pytest.raises(PixxiError) as exc:
        tools.list_leads()
    assert exc.value.status_code == 401
