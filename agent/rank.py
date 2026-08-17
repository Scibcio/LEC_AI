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

LOW_STOCK_THRESHOLD = 15
CONFIDENCE_THRESHOLD = 0.15
PERSISTENCE_CONFIDENCE = 0.30
MAX_EXPOSURE = 1000
CONFIDENCE_FLOOR = 0.5
TIE_BAND = 0.05

# category ordering matches PLAN.md 4.3: coverage > overselling > price > underselling, plus
# "internal" for conflicts where the customer already sees the correct number and only a backend
# source disagrees (e.g. a stale supplier feed); real but nobody's actually affected yet
SEVERITY = {"coverage": 3.5, "overselling": 3.0, "price": 2.0, "underselling": 1.0, "internal": 0.5}
URGENCY = {"coverage": 1.0, "overselling": 0.8, "price": 0.6, "underselling": 0.5, "internal": 0.3}
PHRASES = {
    "coverage": "the reference stock system has no record of a sku other sources are reporting — the trusted count doesn't exist",
    "overselling": "the storefront offers more than physically exists — active oversell risk",
    "underselling": "the storefront under-advertises real stock — lost sales, no broken orders",
    "price": "the storefront price doesn't match the trusted value",
    "internal": "the customer-facing number is already correct — only a backend source disagrees",
}

# derived from config, not hardcoded to "shop", same pattern as detect.py's _REFERENCE
_CUSTOMER_FACING = next(s["name"] for s in SOURCES if s.get("customer_facing"))


def _shop_claim(conflict):
    return next((c for c in conflict.claims if c.source == _CUSTOMER_FACING), None)


def _category(conflict, winner):
    if conflict.type == "coverage":
        return "coverage"
    claim = _shop_claim(conflict)
    if claim is None or claim is winner:
        return "internal"
    if conflict.type == "price":
        return "price"
    return "overselling" if claim.qty > winner.qty else "underselling"


def _exposure(conflict, winner, claim):
    if conflict.type == "coverage":
        # nothing to difference against: the whole advertised position is unbacked
        qty = max((c.qty or 0) for c in conflict.claims)
        price = next((c.price for c in conflict.claims if c.price is not None), 0)
        return qty * price
    if claim is winner:
        return 0.0
    if conflict.type == "price":
        return abs((claim.price or 0) - (winner.price or 0))
    price = claim.price or winner.price or 0
    return abs((claim.qty or 0) - (winner.qty or 0)) * price


def _severity(conflict, winner, category):
    claim = _shop_claim(conflict) or winner
    magnitude = min(1.0, _exposure(conflict, winner, claim) / MAX_EXPOSURE)
    return SEVERITY[category] * (0.8 + 0.4 * magnitude)  # +/-20% within a category, never crosses one


def _priority(severity, confidence, urgency):
    # scale by confidence within [CONFIDENCE_FLOOR, 1], not multiply it raw
    return severity * urgency * (CONFIDENCE_FLOOR + (1 - CONFIDENCE_FLOOR) * confidence)


def _action_type(conflict, winner, category):
    if conflict.type == "stock":
        return RESTOCK if winner.qty < LOW_STOCK_THRESHOLD else ALERT
    return PRICE_ADJUST if category == "price" else ALERT


def _urgency(conflict, category, action_type):
    if action_type == RESTOCK:
        lead_days = next((c.lead_time_days for c in conflict.claims if c.lead_time_days is not None), None)
        if lead_days is not None:
            return min(1.0, lead_days / 7)  # longer lead time = act now, less room to wait
    return URGENCY[category]


def build_action(conflict, now, reliability=None, ticks_seen=1):
    """Resolve one conflict and emit an Action with type, priority, and reason."""
    winner, confidence, resolve_reason = resolve(conflict, now, reliability)
    if classify(conflict, winner, now) == "lag":
        return None  # explained by ordinary lag, not actionable

    category = _category(conflict, winner)
    action_type = _action_type(conflict, winner, category)
    severity = _severity(conflict, winner, category)
    urgency = _urgency(conflict, category, action_type)
    priority = _priority(severity, confidence, urgency)

    if confidence < CONFIDENCE_THRESHOLD:
        action_type = ESCALATE_HUMAN
        summary = f"the winning source leads by only {confidence:.0%} — too thin to act on"
        reason = f"{resolve_reason}, but {summary}, so this escalates instead of guessing."
    elif ticks_seen >= UNRESOLVED_ESCALATE_TICKS and confidence < PERSISTENCE_CONFIDENCE:
        action_type = ESCALATE_HUMAN
        summary = f"unconfident ({confidence:.0%}) and unresolved for {ticks_seen} ticks"
        reason = f"{resolve_reason}, and it is {summary} — a human needs to settle it."
    else:
        summary = PHRASES[category]
        reason = f"{resolve_reason}. {summary}."
        if ticks_seen >= UNRESOLVED_ESCALATE_TICKS:
            reason += f" Open for {ticks_seen} ticks, but the call itself is not in doubt ({confidence:.0%} margin)."

    return Action(
        sku=conflict.sku,
        type=action_type,
        priority=priority,
        category=category,
        reason=reason,
        confidence=confidence,
        summary=summary,
    )


def rank_actions(conflicts, now, reliability=None, ticks_seen=None):
    """All conflicts -> Actions, highest priority first. This is the ranked,
    justified action list the whole project is meant to produce (R3).
    ticks_seen maps a conflict fingerprint to how many ticks it has persisted."""
    ticks_seen = ticks_seen or {}
    actions = []
    for c in conflicts:
        a = build_action(c, now, reliability, ticks_seen.get(fingerprint(c), 1))
        if a is not None:
            actions.append(a)
    actions.sort(key=lambda a: a.priority, reverse=True)
    return actions


def _is_tie(a, b):
    """Two priorities close enough that claiming an order between them would be
    inventing precision the inputs don't support."""
    hi = max(a.priority, b.priority)
    return hi > 0 and abs(a.priority - b.priority) / hi <= TIE_BAND


def explain_ranking(actions):
    lines = []
    for i, a in enumerate(actions):
        line = f"{i + 1}. [{a.type}] {a.sku} (priority {a.priority:.3f}, confidence {a.confidence:.0%}) — {a.reason}"
        if i > 0:
            prev = actions[i - 1]
            if _is_tie(a, prev):
                # never assert an order the numbers don't actually establish
                line += f" Effectively tied with {prev.sku} ({prev.priority:.3f}) — treat both as due now."
            else:
                line += f" Ranked below {prev.sku} ({prev.priority:.3f}, {prev.summary})."
        lines.append(line)
    return "\n".join(lines)


def print_console_table(actions):
    print(f"{'#':<3} {'SKU':<9} {'TYPE':<15} {'PRIORITY':<9} {'CONF':<6} REASON")
    for i, a in enumerate(actions, 1):
        print(f"{i:<3} {a.sku:<9} {a.type:<15} {a.priority:<9.3f} {a.confidence:<6.0%} {a.reason}")


def to_json(actions):
    return json.dumps(
        [
            {
                "sku": a.sku,
                "type": a.type,
                "priority": round(a.priority, 4),
                "confidence": round(a.confidence, 4),
                "category": a.category,
                "reason": a.reason,
            }
            for a in actions
        ],
        indent=2,
    )