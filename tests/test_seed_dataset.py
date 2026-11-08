"""Seed dataset (build step 4): schema, Table 3.4 proportions, determinism, synthetic only."""

import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

from src.pixxi.models import Lead, Listing

ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = ROOT / "data" / "synthetic" / "seed"
PORTALS = {"PROPERTY_FINDER", "BAYUT", "DUBIZZLE"}


def _generator():
    spec = importlib.util.spec_from_file_location("generate_seed", ROOT / "data" / "synthetic" / "generate_seed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(name):
    return json.loads((SEED_DIR / f"{name}.json").read_text(encoding="utf-8"))


def all_leads():
    return _load("leads") + _load("arrivals")


def test_files_match_the_generator_output():
    data = _generator().build_seed()
    for name in ("leads", "arrivals", "listings", "agents", "ground_truth"):
        assert _load(name) == data[name], f"{name}.json is stale: rerun data/synthetic/generate_seed.py"


def test_generator_is_deterministic():
    gen = _generator()
    assert gen.build_seed() == gen.build_seed()


def test_size_and_table_3_4_proportions():
    leads, truth = all_leads(), _load("ground_truth")["leads"]
    assert 10 <= len(leads) <= 20
    sources = Counter("portal" if l["clientSource"] in PORTALS else l["clientSource"] for l in leads)
    assert sources == {"portal": 7, "CLIENT_REFERRAL": 4, "SOCIAL_MEDIA": 3, "EXHIBITION": 1, "COLD_CALL": 1}
    assert Counter(t["stage"] for t in truth) == {"new": 5, "contacted": 6, "qualified": 3, "dormant": 2}
    assert Counter(t["product"] for t in truth)["offplan_apartment"] == 10
    assert Counter(t["compliance"] for t in truth) == {"high": 10, "low": 6}
    assert len({l["nationality"] for l in leads}) == 12


def test_arrivals_are_exactly_the_new_leads():
    new = {t["lead_id"] for t in _load("ground_truth")["leads"] if t["stage"] == "new"}
    assert {l["id"] for l in _load("arrivals")} == new
    assert not new & {l["id"] for l in _load("leads")}


def test_records_parse_with_the_pixxi_models_and_listing_refs_resolve():
    listings = {Listing.model_validate(p).id for p in _load("listings")}
    for raw in all_leads():
        lead = Lead.model_validate(raw)
        if lead.interested_property:
            assert lead.interested_property["id"] in listings


def test_dataset_contains_deliberate_edge_cases():
    truth = _load("ground_truth")["leads"]
    assert any(t["budget_consistent_with_listing"] is False for t in truth)  # inconsistent enquiry
    assert any(not t["has_listing"] for t in truth)  # no listing to read
    assert any(t["sparse_portal_data"] for t in truth)  # low-confidence Layer 1


def test_no_real_personal_data():
    for lead in all_leads():
        assert re.fullmatch(r"Synthetic Lead S\d\d", lead["name"])
        assert lead["email"].endswith("@example.invalid")
        assert lead["phone"].startswith("+9715001000")
