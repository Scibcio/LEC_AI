from collections import defaultdict

from .models import Conflict
from .sources import SOURCES

STOCK_GAP_FLOOR = 5              # units; flat tolerance for sell-through sources (report reserved)
STOCK_ESTIMATE_TOLERANCE = 0.15  # relative tolerance for estimate-only sources (no reserved concept)
PRICE_SPREAD_THRESHOLD = 0.01    # relative spread that counts as real price divergence

# whichever source declares the highest qty authority is the physical-stock reference,
# looked up by value so this isn't hardcoded to "wms" by name (N-source safe)
_QTY_REFERENCE = max(SOURCES, key=lambda s: s["domain_authority"].get("qty", 0))["name"]


def _group_by_sku(records):
    by_sku = defaultdict(list)
    for r in records:
        by_sku[r.sku].append(r)
    return by_sku


def detect_stock_mismatches(records):
    conflicts = []
    for sku, claims in _group_by_sku(records).items():
        reference = next((r for r in claims if r.source == _QTY_REFERENCE), None)
        if reference is None or reference.qty is None:
            continue  # nothing to compare this sku's stock against
        flagged = False
        for r in claims:
            if r.source == _QTY_REFERENCE or r.qty is None:
                continue
            if r.reserved is not None:
                # sell-through source: credit reservations first, then any amount
                # over the reference is a real oversell risk, however small
                effective = r.qty + r.reserved
                gap = reference.qty - effective
                if gap < 0 or gap > STOCK_GAP_FLOOR:
                    flagged = True
                    break
            else:
                # estimate-only source (e.g. supplier): no direction asymmetry,
                # just a looser magnitude check since it was never meant to be exact.
                # relative tolerance is undefined at reference.qty == 0, so fall back
                # to the same absolute floor used above.
                if reference.qty == 0:
                    mismatch = abs(r.qty) > STOCK_GAP_FLOOR
                else:
                    # abs() the denominator too: a negative reference.qty would otherwise
                    # flip the sign of the ratio and it could never clear a positive threshold
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
    return detect_stock_mismatches(records) + detect_price_divergence(records)