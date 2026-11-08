"""Keep the mock and the client honest against the official Pixxi spec (docs/pixxi_openapi.yaml)."""

import re
from pathlib import Path

import pytest
import yaml
from fastapi.routing import APIRoute

from mock_pixxi.app import create_app
from src.pixxi.models import ActivityLog, Lead, Listing, MatchResult, Reminder
from src.pixxi.read_tools import READ_PATHS
from src.pixxi.write_client import WRITE_PATHS

SPEC = yaml.safe_load((Path(__file__).parent.parent / "docs" / "pixxi_openapi.yaml").read_text(encoding="utf-8"))


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


SPEC_OPS = {(m.upper(), _norm(p)) for p, ops in SPEC["paths"].items() for m in ops if m in ("get", "post", "put", "delete", "patch")}


def test_every_mock_pixxi_route_exists_in_spec():
    routes = [r for r in create_app(dispatcher=lambda u, p: None).routes
              if isinstance(r, APIRoute) and r.path.startswith("/pixxiapi")]
    assert routes
    for route in routes:
        for method in route.methods:
            assert (method, _norm(route.path)) in SPEC_OPS, f"{method} {route.path} not in Pixxi spec"


@pytest.mark.parametrize("op", sorted(READ_PATHS | WRITE_PATHS))
def test_every_client_path_exists_in_spec(op):
    method, path = op
    assert (method, _norm(path)) in SPEC_OPS


def _example(path, method="post"):
    return SPEC["paths"][path][method]["responses"]["200"]["content"]["application/json"]["example"]


def _webhook_example(event):
    return SPEC["webhooks"][event]["post"]["requestBody"]["content"]["application/json"]["example"]


def test_spec_lead_list_example_parses():
    leads = [Lead.model_validate(x) for x in _example("/pixxiapi/v1/lead/list")["data"]["list"]]
    assert leads[0].id == 842115
    assert leads[0].budget_min == 10000 and leads[0].budget_max == 100000
    assert leads[0].createTime.tzinfo is not None


def test_spec_listing_example_parses():
    listing = Listing.model_validate(_example("/pixxiapi/v1/properties")["data"]["list"][0])
    assert listing.id == 351047
    assert listing.communityId is None  # the spec example sends the string "null"


def test_spec_match_example_parses():
    match = MatchResult.model_validate(_example("/pixxiapi/v1/matching/listings/passive/page")["data"]["list"][0])
    assert match.matchRate == pytest.approx(99.84)


@pytest.mark.parametrize("event,model", [
    ("ADD_LEADS", Lead), ("UPDATE_LEADS", Lead), ("ADD_LEAD_NOTE", ActivityLog),
    ("ADD_LISTINGS", Listing), ("UPDATE_LISTINGS", Listing), ("ADD_REMINDER", Reminder),
])
def test_spec_webhook_examples_parse(event, model):
    model.model_validate(_webhook_example(event))


def test_receiver_handles_every_subscribable_lead_and_listing_event():
    from src.ingest.webhook_receiver import EVENT_SLUGS

    enum = SPEC["paths"]["/pixxiapi/webhook/v1/subscribe"]["post"]["requestBody"]["content"][
        "application/json"]["schema"]["properties"]["event"]["enum"]
    assert set(EVENT_SLUGS.values()) <= set(enum)
