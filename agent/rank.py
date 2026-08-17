"""
Turns resolved Conflicts into a ranked list of Actions (PLAN.md 4.3). LAG
conflicts produce no action. ERROR conflicts get a type, a severity/urgency
score, and priority = severity * confidence * urgency; a thin trust gap
overrides the type to ESCALATE_HUMAN instead of guessing.
"""

import json

from .models import Action
from .sources import SOURCES
from .state import UNRESOLVED_ESCALATE_TICKS, fingerprint
from .trust import classify, resolve

RESTOCK = "RESTOCK"
PRICE_ADJUST = "PRICE_ADJUST"
ALERT = "ALERT"
ESCALATE_HUMAN = "ESCALATE_HUMAN"

LOW_STOCK_THRESHOLD = 15     # units; below this the resolved truth itself needs reordering
CONFIDENCE_THRESHOLD = 0.03  # below this the agent escalates instead of guessing
MAGNITUDE_REFERENCE = 1000   # dollars; exposure at/above this maxes out the severity bump

# category ordering matches PLAN.md 4.3: overselling > price > underselling, plus "internal"
# for conflicts where the customer already sees the correct number and only a backend
# source disagrees (e.g. a stale supplier feed) — real, but nobody's actually affected yet
SEVERITY_BASE = {"overselling": 3.0, "price": 2.0, "underselling": 1.0, "internal": 0.5}
URGENCY_BASE = {"overselling": 0.8, "price": 0.6, "underselling": 0.5, "internal": 0.3}
CATEGORY_PHRASE = {
    "overselling": "the storefront offers more than physically exists — active oversell risk",
    "underselling": "the storefront under-advertises real stock — lost sales, no broken orders",
    "price": "the storefront price doesn't match the trusted value",
    "internal": "the customer-facing number is already correct — only a backend source disagrees",
}

# derived from config, not hardcoded to "shop", same pattern as detect.py's _QTY_REFERENCE
_CUSTOMER_FACING = next(s["name"] for s in SOURCES if s.get("customer_facing"))


def _customer_facing_claim(conflict):
    return next((c for c in conflict.claims if c.source == _CUSTOMER_FACING), None)


def _severity_category(conflict, winner):
    claim = _customer_facing_claim(conflict)
    if claim is None or claim is winner:
        return "internal"
    if conflict.type == "price":
        return "price"
    return "overselling" if claim.qty > winner.qty else "underselling"


def _dollar_exposure(conflict, winner, claim):
    if claim is winner:
        return 0.0
    if conflict.type == "price":
        return abs((claim.price or 0) - (winner.price or 0))
    price = claim.price or winner.price or 0
    return abs((claim.qty or 0) - (winner.qty or 0)) * price


def _severity(conflict, winner, category):
    claim = _customer_facing_claim(conflict) or winner
    magnitude = min(1.0, _dollar_exposure(conflict, winner, claim) / MAGNITUDE_REFERENCE)
    return SEVERITY_BASE[category] * (0.8 + 0.4 * magnitude)  # +/-20% within a category, never crosses one


def _action_type(conflict, winner, category):
    if conflict.type == "stock":
        return RESTOCK if winner.qty < LOW_STOCK_THRESHOLD else ALERT
    return PRICE_ADJUST if category == "price" else ALERT


def _urgency(conflict, category, action_type):
    if action_type == RESTOCK:
        lead_days = next((c.lead_time_days for c in conflict.claims if c.lead_time_days is not None), None)
        if lead_days is not None:
            return min(1.0, lead_days / 7)  # longer lead time = act now, less room to wait
    return URGENCY_BASE[category]


def build_action(conflict, now, reliability=None, ticks_seen=1):
    """One resolved conflict -> one Action, or None if it's lag. Type comes
    from who's wrong (customer-facing source vs a backend-only source) and
    direction (overselling/underselling); priority = severity * confidence *
    urgency (PLAN.md 4.3). A thin trust gap, or a conflict that's persisted
    across too many ticks, overrides the type to ESCALATE_HUMAN rather than
    letting it sit unresolved forever."""
    winner, confidence, resolve_reason = resolve(conflict, now, reliability)
    if classify(conflict, winner, now) == "lag":
        return None  # explained by ordinary lag, not actionable

    category = _severity_category(conflict, winner)
    action_type = _action_type(conflict, winner, category)
    severity = _severity(conflict, winner, category)
    urgency = _urgency(conflict, category, action_type)
    priority = severity * confidence * urgency

    if confidence < CONFIDENCE_THRESHOLD:
        action_type = ESCALATE_HUMAN
        reason = (
            f"{resolve_reason}, but the trust gap ({confidence:.2f}) is too thin to act on "
            f"automatically — escalating instead of guessing."
        )
    elif ticks_seen >= UNRESOLVED_ESCALATE_TICKS:
        action_type = ESCALATE_HUMAN
        reason = (
            f"{resolve_reason}, and this conflict has persisted {ticks_seen} ticks without "
            f"resolving — escalating rather than re-alerting indefinitely."
        )
    else:
        reason = f"{resolve_reason}. {CATEGORY_PHRASE[category]}."

    return Action(sku=conflict.sku, type=action_type, priority=priority, category=category, reason=reason)


def rank_actions(conflicts, now, reliability=None, ticks_seen=None):
    """All conflicts -> Actions, highest priority first. This is the ranked,
    justified action list the whole project is meant to produce (R3).
    ticks_seen maps a conflict fingerprint to how many ticks it has persisted."""
    ticks_seen = ticks_seen or {}
    actions = [
        a
        for a in (
            build_action(c, now, reliability, ticks_seen.get(fingerprint(c), 1)) for c in conflicts
        )
        if a is not None
    ]
    actions.sort(key=lambda a: a.priority, reverse=True)
    return actions


def explain_ranking(actions):
    lines = []
    for i, a in enumerate(actions):
        line = f"{i + 1}. [{a.type}] {a.sku} (priority {a.priority:.3f}) — {a.reason}"
        if i > 0:
            prev = actions[i - 1]
            line += (
                f" Ranked below {prev.sku} ({prev.priority:.3f}, {CATEGORY_PHRASE[prev.category]})."
            )
        lines.append(line)
    return "\n".join(lines)


def print_console_table(actions):
    print(f"{'#':<3} {'SKU':<9} {'TYPE':<15} {'PRIORITY':<9} REASON")
    for i, a in enumerate(actions, 1):
        print(f"{i:<3} {a.sku:<9} {a.type:<15} {a.priority:<9.3f} {a.reason}")


def to_json(actions):
    return json.dumps(
        [
            {"sku": a.sku, "type": a.type, "priority": round(a.priority, 4), "category": a.category, "reason": a.reason}
            for a in actions
        ],
        indent=2,
    )