"""Seed dataset generator (build step 4): 16 synthetic leads and 14 listings in Pixxi's schema.

Deterministic (fixed random seed), so every run produces the same files and the dataset
can be cited in the thesis. Proportions follow the proposal's Table 3.4 as closely as 16
leads allow (the full 100-lead set is step 5):

| Attribute        | Table 3.4                                   | Seed (16 leads)        |
| ---------------- | ------------------------------------------- | ---------------------- |
| Enquiry source   | portal 45, referral 22, social 18,          | 7 / 4 / 3 / 1 / 1      |
|                  | exhibition 10, cold call 5 percent          |                        |
| Stage            | new 30, contacted 35, qualified 20,         | 5 / 6 / 3 / 2          |
|                  | dormant 15 percent                          |                        |
| Off-plan apt.    | 63 percent                                  | 10 of 16 (62.5 %)      |
| Nationalities    | 12                                          | 12                     |
| Compliance       | 60 high / 40 low                            | 10 / 6                 |

Output (data/synthetic/seed/):
- leads.json        leads already in Pixxi (stages contacted, qualified, dormant)
- arrivals.json     the "new" leads, delivered as ADD_LEADS events during a run
- listings.json     listings the leads enquire about
- ground_truth.json per-lead labels known by construction (stage, compliance, intended
                    profile), plus the tagId -> stage map used by the mock

Everything is synthetic: names are "Synthetic Lead Snn", e-mails use the reserved
.invalid domain, phone numbers are a fixed placeholder range. Nationality and gender are
present only because the Pixxi schema has them; the middleware never uses them for scoring
or sends them to the LLM.

Run:  uv run python data/synthetic/generate_seed.py
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SEED = 4  # build step 4
OUT_DIR = Path(__file__).parent / "seed"
TZ = ZoneInfo("Asia/Dubai")
# Fixed reference "now" so relative dates (dormancy etc.) are reproducible.
REFERENCE_TIME = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
TIME_FMT = "%Y-%m-%d %H:%M:%S"

LEAD_ID_START = 910001
LISTING_ID_START = 352001

# Lead stage (Pixxi tagId). Tag IDs are tenant-specific and undocumented; this map is a
# mock-only assumption (docs/api_findings.md).
STAGE_TAG = {"new": 39, "contacted": 40, "qualified": 41, "dormant": 42}

AGENTS = [
    {"id": 2031, "name": "Synthetic Agent A", "email": "agent.a@example.invalid", "phone": "+971509000001"},
    {"id": 2032, "name": "Synthetic Agent B", "email": "agent.b@example.invalid", "phone": "+971509000002"},
    {"id": 2033, "name": "Synthetic Agent C", "email": "agent.c@example.invalid", "phone": "+971509000003"},
]

NATIONALITIES = [
    "India", "Pakistan", "United Kingdom", "Russia", "China", "Egypt",
    "United Arab Emirates", "Saudi Arabia", "Germany", "France", "Nigeria", "Lebanon",
]

# Synthetic location IDs (the first three match mock_pixxi/fixtures).
REGIONS = {
    "Business Bay": {"regionId": 48, "community": "The Metropolis Tower", "communityId": 388, "psf": 2100},
    "Arabian Ranches": {"regionId": 61, "community": "Arabian Ranches 3", "communityId": 512, "psf": 1350},
    "Dubai Marina": {"regionId": 70, "community": "Marina Promenade", "communityId": 601, "psf": 1900},
    "Jumeirah Village Circle": {"regionId": 83, "community": "District 12", "communityId": 701, "psf": 1250},
    "Dubai Hills Estate": {"regionId": 66, "community": "Park Heights", "communityId": 530, "psf": 2000},
    "Downtown Dubai": {"regionId": 52, "community": "Opera District", "communityId": 420, "psf": 2900},
}

# Portal sources are split across the three UAE portals.
SOURCES = (["PROPERTY_FINDER"] * 3 + ["BAYUT"] * 2 + ["DUBIZZLE"] * 2
           + ["CLIENT_REFERRAL"] * 4 + ["SOCIAL_MEDIA"] * 3 + ["EXHIBITION"] + ["COLD_CALL"])
PORTALS = {"PROPERTY_FINDER": "Property Finder", "BAYUT": "Bayut", "DUBIZZLE": "Dubizzle"}
STAGES = ["new"] * 5 + ["contacted"] * 6 + ["qualified"] * 3 + ["dormant"] * 2
# 10 off-plan apartments; the other 6 cover ready apartments, villas/townhouses and rentals.
PRODUCTS = (["offplan_apartment"] * 10 + ["ready_apartment"] * 2 + ["villa"] * 2 + ["rent_apartment"] * 2)
COMPLIANCE = ["high"] * 10 + ["low"] * 6


def _fmt(dt: datetime) -> str:
    return dt.strftime(TIME_FMT)


def _round_price(value: float, step: int = 10_000) -> int:
    return int(round(value / step) * step)


def build_listings(rng: random.Random) -> list[dict]:
    listings: list[dict] = []
    next_id = LISTING_ID_START

    def add(region: str, kind: str, beds: int, size: int) -> None:
        nonlocal next_id
        r = REGIONS[region]
        is_rent = kind == "rent_apartment"
        is_villa = kind == "villa"
        listing_type = "RENT" if is_rent else ("NEW" if kind == "offplan_apartment" else "SELL")
        price = _round_price(size * r["psf"] * rng.uniform(0.92, 1.08))
        if is_rent:
            price = _round_price(price * 0.065, 1_000)  # annual rent
        bed_label = "Studio" if beds == 0 else f"{beds}BR"
        type_label = "Villa" if is_villa else "Apartment"
        status_label = {"NEW": "Off-plan", "SELL": "Ready", "RENT": "For rent"}[listing_type]
        listing = {
            "id": next_id,
            "propertyId": f"10031{next_id:08d}",
            "title": f"Synthetic {bed_label} {status_label} {type_label}, {region}",
            "listingType": listing_type,
            "propertyType": ["VILLA" if is_villa else "APARTMENT"],
            "status": "ACTIVE",
            "price": price,
            "bedRooms": beds,
            "size": size,
            "region": region,
            "regionId": str(r["regionId"]),  # string in listings, as in the real API
            "cityId": 41,
            "cityName": "Dubai",
            "community": r["community"],
            "communityId": str(r["communityId"]),
            "developer": f"Synthetic Developer {1 + (next_id % 4)}",
            "developerId": str(10 + (next_id % 4)),
            "isFurniture": "NONE" if listing_type == "NEW" else "SEMI",
            "agent": {k: AGENTS[next_id % 3][k] for k in ("name", "email", "phone")},
            "photos": [],
            "amenities": ["Shared Pool", "Gym"] if not is_villa else ["Private Garden", "Maid's Room"],
            "createTime": _fmt(REFERENCE_TIME - timedelta(days=rng.randint(30, 200))),
            "updateTime": _fmt(REFERENCE_TIME - timedelta(days=rng.randint(1, 29))),
        }
        if listing_type == "NEW":
            listing["newParam"] = {"handoverTime": f"{rng.choice([2027, 2028, 2029])}-0{rng.randint(1, 9)}-01 00:00:00",
                                   "completionStatus": "OFF_PLAN"}
        elif listing_type == "SELL":
            listing["sellParam"] = {"completionStatus": "COMPLETED", "buildYear": str(rng.randint(2012, 2024)),
                                    "bathrooms": beds + 1}
        else:
            listing["rentParam"] = {"completionStatus": "COMPLETED", "rentalPeriod": "YEARLY"}
        listings.append(listing)
        next_id += 1

    for region in ("Business Bay", "Jumeirah Village Circle", "Dubai Hills Estate", "Downtown Dubai", "Dubai Marina"):
        add(region, "offplan_apartment", 1, 780)
        add(region, "offplan_apartment", 2, 1150)
    add("Dubai Marina", "ready_apartment", 1, 820)
    add("Downtown Dubai", "ready_apartment", 2, 1250)
    add("Arabian Ranches", "villa", 4, 3200)
    add("Jumeirah Village Circle", "rent_apartment", 1, 750)
    return listings


def _pick_listing(rng: random.Random, listings: list[dict], product: str) -> dict:
    wanted = {"offplan_apartment": ("NEW", "APARTMENT"), "ready_apartment": ("SELL", "APARTMENT"),
              "villa": ("SELL", "VILLA"), "rent_apartment": ("RENT", "APARTMENT")}[product]
    pool = [l for l in listings if (l["listingType"], l["propertyType"][0]) == wanted]
    return rng.choice(pool)


def _stage_times(rng: random.Random, stage: str) -> tuple[datetime, datetime]:
    if stage == "new":
        created = REFERENCE_TIME - timedelta(hours=rng.randint(0, 36), minutes=rng.randint(0, 59))
        return created, created
    if stage == "contacted":
        created = REFERENCE_TIME - timedelta(days=rng.randint(3, 20))
        return created, created + timedelta(days=rng.randint(1, 2))
    if stage == "qualified":
        created = REFERENCE_TIME - timedelta(days=rng.randint(10, 40))
        return created, REFERENCE_TIME - timedelta(days=rng.randint(1, 6))
    # dormant: no activity for longer than the 30-day dormancy threshold
    created = REFERENCE_TIME - timedelta(days=rng.randint(70, 140))
    return created, REFERENCE_TIME - timedelta(days=rng.randint(35, 60))


def _portal_note(source: str, listing: dict | None, product: str) -> str:
    if source in PORTALS:
        return f"Enquired via {PORTALS[source]} about '{listing['title']}'." if listing else \
            f"Enquired via {PORTALS[source]}; no specific listing."
    if source == "CLIENT_REFERRAL":
        return "Referred by an existing client." + (f" Interested in '{listing['title']}'." if listing else "")
    if source == "SOCIAL_MEDIA":
        return "Instagram ad form submission." + (f" Ad for '{listing['title']}'." if listing else "")
    if source == "EXHIBITION":
        return "Met at a property exhibition stand; asked about off-plan payment plans."
    return "Cold-call list; interest not yet confirmed."


def build_leads(rng: random.Random, listings: list[dict]) -> tuple[list[dict], list[dict]]:
    sources, stages, products, compliance = SOURCES[:], STAGES[:], PRODUCTS[:], COMPLIANCE[:]
    for seq in (sources, stages, products, compliance):
        rng.shuffle(seq)
    # Cold calls and exhibitions rarely concern rentals; swap a rental product away if paired.
    for i, src in enumerate(sources):
        if src in ("COLD_CALL", "EXHIBITION") and products[i] == "rent_apartment":
            j = next(k for k, p in enumerate(products) if p == "offplan_apartment" and sources[k] not in ("COLD_CALL", "EXHIBITION"))
            products[i], products[j] = products[j], products[i]

    leads, truth = [], []
    for i in range(16):
        lead_id = LEAD_ID_START + i
        source, stage, product = sources[i], stages[i], products[i]
        listing = _pick_listing(rng, listings, product)
        # Portal and social leads come from a listing; cold calls and exhibitions do not.
        has_listing = source in PORTALS or (source == "SOCIAL_MEDIA" and i % 3 != 0) or \
            (source == "CLIENT_REFERRAL" and i % 2 == 0)
        agent = AGENTS[i % 3]
        created, updated = _stage_times(rng, stage)
        region = REGIONS[listing["region"]]
        price = listing["price"]

        # Budget brackets the listing price; two leads that enquired about a listing get a budget
        # well below its price (an inconsistent enquiry Agent 1 should notice).
        mismatch = has_listing and i in (7, 10)
        low, high = (price * 0.55, price * 0.75) if mismatch else (price * rng.uniform(0.8, 0.95), price * rng.uniform(1.05, 1.25))
        budget = f"{_round_price(low)}|{_round_price(high)}"
        beds = listing["bedRooms"]
        rooms = f"{max(beds - 1, 0) or 'Studio'}|{beds}" if rng.random() < 0.5 else f"{beds}|{beds + 1}"
        sparse = source in ("COLD_CALL", "EXHIBITION")  # thin portal data -> low-confidence Layer 1

        is_offplan = product == "offplan_apartment"
        lead = {
            "id": lead_id,
            "createBy": agent["id"],
            "updateBy": agent["id"],
            "createTime": _fmt(created),
            "updateTime": _fmt(updated),
            "clientType": "RENT" if product == "rent_apartment" else "BUY",
            "tagId": STAGE_TAG[stage],
            "name": f"Synthetic Lead S{i + 1:02d}",
            "phone": f"+9715001000{i + 1:02d}",
            "email": f"seed{i + 1:02d}@example.invalid",
            "nationality": NATIONALITIES[i % len(NATIONALITIES)],
            "gender": "UNKNOWN",
            "houseType": None if sparse else listing["propertyType"],
            "rooms": None if sparse else rooms,
            "budget": None if source == "COLD_CALL" else budget,
            "size": None if sparse else f"{int(listing['size'] * 0.8)}|{int(listing['size'] * 1.3)}",
            "payMethod": None if sparse or product == "rent_apartment" else rng.choice(["CASH", "MORTGAGE", None]),
            "regionId": region["regionId"],
            "communityId": None if sparse else region["communityId"],
            "region": listing["region"],
            "community": None if sparse else region["community"],
            "clientSource": source,
            "status": "ACTIVE",
            "agentId": agent["id"],
            "agentInfo": dict(agent),
            "projectType": (["OFF_PLAN"] if is_offplan else ["COMPLETED"]) if not sparse else None,
            "buyerType": None if sparse or product == "rent_apartment" else ("INVESTOR" if is_offplan and rng.random() < 0.7 else "OWNED"),
            "notes": _portal_note(source, listing if has_listing else None, product),
            "property": {"id": listing["id"], "propertyId": listing["propertyId"], "title": listing["title"]} if has_listing else None,
            "reminderCount": 0 if stage in ("new", "dormant") else rng.randint(1, 3),
            "isDuplicated": False,
            "customFields": None,
        }
        leads.append(lead)
        truth.append({
            "lead_id": lead_id,
            "stage": stage,
            "compliance": compliance[i],
            "source_group": "portal" if source in PORTALS else source.lower(),
            "product": product,
            "has_listing": has_listing,
            "budget_consistent_with_listing": None if lead["budget"] is None or not has_listing else not mismatch,
            "sparse_portal_data": sparse,
        })
    return leads, truth


def build_seed(seed: int = SEED) -> dict[str, list[dict] | dict]:
    rng = random.Random(seed)
    listings = build_listings(rng)
    leads, truth = build_leads(rng, listings)
    stage_of = {t["lead_id"]: t["stage"] for t in truth}
    return {
        "leads": [l for l in leads if stage_of[l["id"]] != "new"],
        "arrivals": [l for l in leads if stage_of[l["id"]] == "new"],
        "listings": listings,
        "agents": AGENTS,
        "ground_truth": {
            "seed": seed,
            "reference_time": _fmt(REFERENCE_TIME),
            "stage_tag_map": STAGE_TAG,
            "leads": truth,
        },
    }


def write_seed(out_dir: Path = OUT_DIR, seed: int = SEED) -> dict:
    data = build_seed(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in data.items():
        (out_dir / f"{name}.json").write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


if __name__ == "__main__":
    d = write_seed()
    print(f"Wrote {len(d['leads'])} leads, {len(d['arrivals'])} arrivals, {len(d['listings'])} listings to {OUT_DIR}")
