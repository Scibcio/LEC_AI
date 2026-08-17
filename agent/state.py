"""
Persisted state across ticks: which conflicts have already been seen (so a
drifting-but-ongoing conflict doesn't look new) and per-source-per-field
reliability, learned from which claims won/lost past resolutions. This is
the sole mechanism behind "confidence thresholds adapt" (PLAN.md 4.4) —
reliability feeds back into the same trust formula from trust.py, so a
chronically-wrong source needs a bigger edge to win next time. No separate
threshold store is needed.
"""

import json

from .detect import PRICE_SPREAD_THRESHOLD, STOCK_GAP_FLOOR
from .sources import REPO_ROOT, SOURCES
from .trust import NEUTRAL_RELIABILITY

STATE_FILE = REPO_ROOT / "state.json"
RELIABILITY_LEARNING_RATE = 0.05  # how much one resolved ERROR conflict moves a score
RELIABILITY_DECAY_RATE = 0.02  # per-tick pull back toward neutral
UNRESOLVED_ESCALATE_TICKS = 3  # ticks_seen at/above this forces escalation regardless of confidence


def _empty_reliability():
    return {s["name"]: {field: NEUTRAL_RELIABILITY for field in s["domain_authority"]} for s in SOURCES}


def load_state():
    if not STATE_FILE.exists():
        return {"conflicts": {}, "reliability": _empty_reliability()}
    with open(STATE_FILE) as f:
        state = json.load(f)
    state.setdefault("conflicts", {})
    state.setdefault("reliability", {})
    for name, fields in _empty_reliability().items():
        state["reliability"].setdefault(name, {})
        for field, neutral in fields.items():
            state["reliability"][name].setdefault(field, neutral)  # covers a source added since the last save
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fingerprint(conflict):
    return f"{conflict.sku}:{conflict.type}"  # not on values: drift shouldn't look like a new conflict


def record_conflict(state, conflict, now):
    fp = fingerprint(conflict)
    now_iso = now.isoformat()
    entry = state["conflicts"].get(fp)
    if entry is None:
        entry = {"first_seen": now_iso, "last_seen": now_iso, "ticks_seen": 1}
    else:
        entry["last_seen"] = now_iso
        entry["ticks_seen"] += 1
    state["conflicts"][fp] = entry
    return entry


def _agrees_with_winner(conflict, claim, winner):
    field = "qty" if conflict.type == "stock" else "price"
    winner_value, claim_value = getattr(winner, field), getattr(claim, field)
    if conflict.type == "stock":
        if claim.reserved is not None:
            claim_value = claim_value + claim.reserved  # credit reservations, same as detect.py
        return abs(winner_value - claim_value) <= STOCK_GAP_FLOOR
    return abs(winner_value - claim_value) / max(abs(winner_value), 0.01) <= PRICE_SPREAD_THRESHOLD


def update_reliability(state, conflict, winner, verdict):
    if verdict != "error":
        return  # lag isn't evidence anyone was wrong
    field = "qty" if conflict.type == "stock" else "price"
    reliability = state["reliability"]
    reliability.setdefault(winner.source, {})
    reliability[winner.source][field] = min(
        1.0, reliability[winner.source].get(field, NEUTRAL_RELIABILITY) + RELIABILITY_LEARNING_RATE
    )
    for c in conflict.claims:
        if c is winner or getattr(c, field) is None:
            continue
        if _agrees_with_winner(conflict, c, winner):
            continue  # this claim actually matched the resolved truth -- not evidence it was wrong
        reliability.setdefault(c.source, {})
        reliability[c.source][field] = max(
            0.0, reliability[c.source].get(field, NEUTRAL_RELIABILITY) - RELIABILITY_LEARNING_RATE
        )


def decay_reliability(state):
    for fields in state["reliability"].values():
        for field, score in fields.items():
            fields[field] = score + (NEUTRAL_RELIABILITY - score) * RELIABILITY_DECAY_RATE