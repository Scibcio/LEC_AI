"""
Add new sources to the SOURCES list below. Each source should have a unique name,
 - path to its data file,
 - domain authority weights for quantity and price,
 - staleness window in minutes,
 - parsing function that extracts the relevant fields from the raw data.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Record

REPO_ROOT = Path(__file__).resolve().parent.parent  # anchor data/state paths to repo, not caller's cwd


def _parse_wms(raw):
    return {"qty": raw.get("on_hand_qty"), "price": None, "last_updated": raw["last_synced"]}


def _parse_shop(raw):
    return {
        "qty": raw.get("available_to_sell"),
        "reserved": raw.get("reserved", 0),  # missing on one record != "no concept of reservations"
        "price": raw.get("listed_price"),
        "last_updated": raw["updated_at"],
    }


def _parse_supplier(raw):
    return {
        "qty": raw.get("estimated_stock"),
        "price": raw.get("list_price"),
        "lead_time_days": raw.get("lead_time_days"),  # used for restock urgency in rank.py
        "last_updated": raw["reported_at"],
    }


SOURCES = [
    {
        "name": "wms",
        "data_file": "data/wms.json",
        "domain_authority": {"qty": 1.0, "price": 0.1},
        "staleness_window_minutes": 15,
        "parse": _parse_wms,
    },
    {
        "name": "shop",
        "data_file": "data/shop.json",
        "domain_authority": {"qty": 0.4, "price": 0.5},
        "staleness_window_minutes": 60,
        "parse": _parse_shop,
        "customer_facing": True,  # this is what a customer actually sees; rank.py uses it to
                                   # decide PRICE_ADJUST-vs-ALERT and stock severity, not by name
    },
    {
        "name": "supplier",
        "data_file": "data/supplier.json",
        "domain_authority": {"qty": 0.3, "price": 0.9},
        "staleness_window_minutes": 1440,
        "parse": _parse_supplier,
    },
]

# detect.py keys records by source name; a copy-paste duplicate would silently
# merge two sources' data instead of erroring, so catch it at import time
assert len({s["name"] for s in SOURCES}) == len(SOURCES), "duplicate name in SOURCES"


def _parse_timestamp(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # fixtures always have Z, but don't crash if one doesn't
    return dt


def load_source(config):
    path = REPO_ROOT / config["data_file"]
    with open(path) as f:
        data = json.load(f)
    records = []
    for raw in data["records"]:
        fields = config["parse"](raw)
        records.append(
            Record(
                sku=raw["sku"],  # required identity field: crash if missing
                source=config["name"],
                qty=fields.get("qty"),  # legitimately optional per source
                price=fields.get("price"),  # e.g. WMS never reports price
                last_updated=_parse_timestamp(fields["last_updated"]),  # required
                reserved=fields.get("reserved"),  # only shop has this
                lead_time_days=fields.get("lead_time_days"),  # only supplier has this
            )
        )
    return records


def load_all():
    records = []
    for config in SOURCES:
        records.extend(load_source(config))
    return records


def snapshot_as_of():
    """When the fixture data was captured. A frozen snapshot declares its own
    'now' so the demo stays reproducible instead of decaying as real time moves
    past it; a live feed omits as_of and the caller falls back to real time."""
    stamps = []
    for config in SOURCES:
        with open(REPO_ROOT / config["data_file"]) as f:
            as_of = json.load(f).get("as_of")
        if as_of:
            stamps.append(_parse_timestamp(as_of))
    return max(stamps) if stamps else None