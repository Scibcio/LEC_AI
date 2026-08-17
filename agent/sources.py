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

REPO_ROOT = Path(__file__).resolve().parent.parent


def _parse_wms(raw):
    return {"qty": raw.get("on_hand_qty"), "price": None, "last_updated": raw["last_synced"]}


def _parse_shop(raw):
    return {
        "qty": raw.get("available_to_sell"),
        "reserved": raw.get("reserved", 0),
        "price": raw.get("listed_price"),
        "last_updated": raw["updated_at"],
    }


def _parse_supplier(raw):
    return {
        "qty": raw.get("estimated_stock"),
        "price": raw.get("list_price"),
        "lead_time_days": raw.get("lead_time_days"),
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
        "customer_facing": True,
    },
    {
        "name": "supplier",
        "data_file": "data/supplier.json",
        "domain_authority": {"qty": 0.3, "price": 0.9},
        "staleness_window_minutes": 1440,
        "parse": _parse_supplier,
    },
]

# catch duplicate names at import time
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
                sku=raw["sku"],
                source=config["name"],
                qty=fields.get("qty"),
                price=fields.get("price"),
                last_updated=_parse_timestamp(fields["last_updated"]),
                reserved=fields.get("reserved"),
                lead_time_days=fields.get("lead_time_days"),
            )
        )
    return records


def load_all():
    records = []
    for config in SOURCES:
        records.extend(load_source(config))
    return records


def snapshot_as_of():
    """When the fixture data was captured. Freezes the clock for reproducibility."""
    stamps = []
    for config in SOURCES:
        with open(REPO_ROOT / config["data_file"]) as f:
            as_of = json.load(f).get("as_of")
        if as_of:
            stamps.append(_parse_timestamp(as_of))
    return max(stamps) if stamps else None