from collections import defaultdict

from .models import Conflict
from .sources import SOURCES

STOCK_GAP_FLOOR = 5
STOCK_ESTIMATE_TOLERANCE = 0.15
PRICE_SPREAD_THRESHOLD = 0.01

_REFERENCE = max(SOURCES, key=lambda s: s["domain_authority"].get("qty", 0))["name"]


def _group_by_sku(records):
    by_sku = defaultdict(list)
    for r in records:
        by_sku[r.sku].append(r)
    return by_sku


def detect_coverage_gaps(records):
    """Flag SKUs missing from the reference source but reported elsewhere."""
    conflicts = []
    for sku, claims in _group_by_sku(records).items():
        reference = next((r for r in claims if r.source == _REFERENCE), None)
        if reference is not None and reference.qty is not None:
            continue  # reference covers this sku; ordinary stock comparison applies
        if not any(r.qty is not None for r in claims):
            continue  # nobody reports stock for this sku at all -- not a coverage gap
        conflicts.append(Conflict(sku=sku, type="coverage", claims=claims))
    return conflicts


def detect_stock_mismatches(records):
    conflicts = []
    for sku, claims in _group_by_sku(records).items():
        reference = next((r for r in claims if r.source == _REFERENCE), None)
        if reference is None or reference.qty is None:
            continue  # no reference to compare against -- detect_coverage_gaps owns this case
        flagged = False
        for r in claims:
            if r.source == _REFERENCE or r.qty is None:
                continue
            if r.reserved is not None:
                # credit reservations before comparing to reference
                effective = r.qty + r.reserved
                gap = reference.qty - effective
                if gap < 0 or gap > STOCK_GAP_FLOOR:
                    flagged = True
                    break
            else:
                if reference.qty == 0:
                    mismatch = abs(r.qty) > STOCK_GAP_FLOOR
                else:
                    # abs() denominator: negative reference.qty would flip sign
                    mismatch = abs(reference.qty - r.qty) / abs(reference.qty) > STOCK_ESTIMATE_TOLERANCE
                if mismatch:
                    flagged = True
                    break
        if flagged:
            conflicts.append(Conflict(sku=sku, type="stock", claims=claims))
    return conflicts


def detect_price_divergence(records):
    conflicts = []
    for sku, claims in _group_by_sku(records).items():
        priced = [r for r in claims if r.price is not None]  # e.g. WMS never reports price
        if len(priced) < 2:
            continue  # need at least 2 claims to be able to disagree
        prices = [r.price for r in priced]
        hi = max(prices)
        if hi <= 0:
            continue  # nothing to compute a relative spread against (e.g. a free/unpriced SKU)
        spread = (hi - min(prices)) / hi
        if spread > PRICE_SPREAD_THRESHOLD:
            conflicts.append(Conflict(sku=sku, type="price", claims=priced))
    return conflicts


def detect_all(records):
    return detect_coverage_gaps(records) + detect_stock_mismatches(records) + detect_price_divergence(records)