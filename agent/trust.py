"""
Reasoning core: how fresh/trustworthy a claim is, which claim wins a conflict,
whether that conflict is lag or error, and the naive majority-vote comparison
kept only to contrast against the real resolver (see PLAN.md 4.1-4.3).
"""

from collections import Counter

from .detect import STOCK_ESTIMATE_TOLERANCE, STOCK_GAP_FLOOR, _REFERENCE
from .models import field_for
from .sources import SOURCES

NEUTRAL_RELIABILITY = 0.5  # default when no learned score exists yet (new source, or no state passed in)

_SOURCE_BY_NAME = {s["name"]: s for s in SOURCES}


def freshness(record, now):
    window = _SOURCE_BY_NAME[record.source]["staleness_window_minutes"]
    age_minutes = max((now - record.last_updated).total_seconds() / 60, 0)
    return min(1.0, window / max(age_minutes, 1))  # 1.0 inside the window, tapers off past it


def trust(record, field, now, reliability=NEUTRAL_RELIABILITY):
    authority = _SOURCE_BY_NAME[record.source]["domain_authority"].get(field, 0.0)
    return authority * freshness(record, now) * reliability


def resolve(conflict, now, reliability=None):
    """Pick the highest-trust claim; return it with confidence margin and reason."""
    field = field_for(conflict)

    def _reliability_for(record):
        # state.py's learned per-source-per-field score. Falls back to neutral for a
        # source with no history yet (e.g. just added to the registry), or if no state
        # dict was passed at all.
        if reliability is None:
            return NEUTRAL_RELIABILITY
        return reliability.get(record.source, {}).get(field, NEUTRAL_RELIABILITY)

    scored = sorted(
        ((c, trust(c, field, now, _reliability_for(c))) for c in conflict.claims if getattr(c, field) is not None),
        key=lambda pair: pair[1],
        reverse=True,
    )
    winner, winner_trust = scored[0]
    runner_up, runner_up_trust = scored[1] if len(scored) > 1 else (None, 0.0)
    # relative margin in [0, 1]; a winner with zero trust has nothing to be confident about.
    # Single uncorroborated claims have confidence 0.0: no competing evidence
    if len(scored) == 1:
        confidence = 0.0
    else:
        confidence = (winner_trust - runner_up_trust) / winner_trust if winner_trust > 0 else 0.0
    runner_up_desc = f"{runner_up.source} ({runner_up_trust:.2f})" if runner_up else "no other claim"
    reason = f"trusted {winner.source} ({winner_trust:.2f}) on {field} over {runner_up_desc}"
    return winner, confidence, reason


def _is_lag(reference, claim, now):
    window = _SOURCE_BY_NAME[claim.source]["staleness_window_minutes"]
    age_minutes = max((now - claim.last_updated).total_seconds() / 60, 0)
    if claim.reserved is not None:
        # sell-through source: same complement of detect.py's flat gap-floor check
        gap = reference.qty - (claim.qty + claim.reserved)
        within_tolerance = 0 <= gap <= STOCK_GAP_FLOOR
    elif reference.qty == 0:
        within_tolerance = abs(claim.qty) <= STOCK_GAP_FLOOR
    else:
        # estimate-only source: same complement of detect.py's relative-tolerance check
        within_tolerance = abs(reference.qty - claim.qty) / abs(reference.qty) <= STOCK_ESTIMATE_TOLERANCE
    return age_minutes <= window and within_tolerance


def classify(conflict, winner, now):
    """"lag" (expected, temporary, no action) or "error" (real, actionable) --
    PLAN.md 4.2's rule: age within window + benign direction (+ small enough
    gap, for stock) means lag; anything else is error."""
    if conflict.type == "coverage":
        return "error"  # a missing reference record is never explained by ordinary lag

    if conflict.type == "stock":
        reference = next((c for c in conflict.claims if c.source == _REFERENCE), None)
        if reference is None or reference.qty is None:
            return "error"
        # every non-reference claim already failed detect.py's tolerance to become a
        # Conflict in the first place, so this loop is expected to always land on
        # "error" today — re-checked here rather than assumed, so it stays correct
        # if the tolerances ever change independently
        for c in conflict.claims:
            if c.source == _REFERENCE or c.qty is None:
                continue
            if not _is_lag(reference, c, now):
                return "error"
        return "lag"

    # price: lag only if every losing claim genuinely hasn't seen the winner's
    # change yet (its own last sync predates the winner's) and is still within
    # its own staleness window; otherwise it had the chance and missed it
    for c in conflict.claims:
        if c is winner or c.price is None:
            continue
        window = _SOURCE_BY_NAME[c.source]["staleness_window_minutes"]
        age_minutes = max((now - c.last_updated).total_seconds() / 60, 0)
        missed_update = c.last_updated > winner.last_updated
        if missed_update or age_minutes > window:
            return "error"
    return "lag"


def majority_vote(conflict):
    field = field_for(conflict)
    values = [getattr(c, field) for c in conflict.claims if getattr(c, field) is not None]
    return Counter(values).most_common(1)[0][0]  # ties broken by first-seen order, deterministic