"""State persisted across ticks: conflict tracking and learned reliability."""

import json
import os
import sys
import tempfile

from .detect import PRICE_SPREAD_THRESHOLD, STOCK_GAP_FLOOR
from .models import field_for
from .sources import REPO_ROOT, SOURCES
from .trust import NEUTRAL_RELIABILITY

STATE_FILE = REPO_ROOT / "state.json"
RELIABILITY_LEARNING_RATE = 0.05  # how much one corroborated ERROR conflict moves a score
# Must exceed LEARNING_RATE / |NEUTRAL - floor| = 0.05 / 0.5 = 0.10, or decay can never
# balance a source that loses every tick and scores run away to the 0.0/1.0 rails.
RELIABILITY_DECAY_RATE = 0.12  # per-tick pull back toward neutral
UNRESOLVED_ESCALATE_TICKS = 3  # ticks at/above this escalate -- but only if still unconfident


def _empty_reliability():
    return {s["name"]: {field: NEUTRAL_RELIABILITY for field in s["domain_authority"]} for s in SOURCES}


def load_state():
    if not STATE_FILE.exists():
        return {"conflicts": {}, "reliability": _empty_reliability()}
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError("state root is not an object")
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        # a truncated or clobbered state file must not take the agent down --
        # under --loop that would end the run permanently. Start clean and say so.
        print(f"warning: {STATE_FILE.name} unreadable ({exc}); starting from empty state", file=sys.stderr)
        return {"conflicts": {}, "reliability": _empty_reliability()}
    state.setdefault("conflicts", {})
    state.setdefault("reliability", {})
    for name, fields in _empty_reliability().items():
        state["reliability"].setdefault(name, {})
        for field, neutral in fields.items():
            state["reliability"][name].setdefault(field, neutral)  # covers a source added since the last save
    return state


def save_state(state):
    """Write-and-rename, so a crash or a second process mid-write can never
    leave a half-written state.json behind. rename() is atomic on POSIX and
    on Windows for same-directory targets via os.replace."""
    fd, tmp = tempfile.mkstemp(dir=str(STATE_FILE.parent), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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


def _agrees(conflict, claim, winner):
    field = field_for(conflict)
    winner_value, claim_value = getattr(winner, field), getattr(claim, field)
    if winner_value is None or claim_value is None:
        return False
    if conflict.type != "price":
        if claim.reserved is not None:
            claim_value = claim_value + claim.reserved  # credit reservations, same as detect.py
        return abs(winner_value - claim_value) <= STOCK_GAP_FLOOR
    return abs(winner_value - claim_value) / max(abs(winner_value), 0.01) <= PRICE_SPREAD_THRESHOLD


def update_reliability(state, resolutions):
    """Update reliability from all tick's resolutions, batched by source-field."""
    # only learn when an independent second source corroborates the winner
    tally = {}  # (source, field) -> [wins, losses]
    for conflict, winner, verdict in resolutions:
        if verdict != "error":
            continue  # lag isn't evidence anyone was wrong
        field = field_for(conflict)
        others = [c for c in conflict.claims if c is not winner and getattr(c, field) is not None]
        if not any(_agrees(conflict, c, winner) for c in others):
            continue  # winner stands alone: the formula's own opinion, not evidence
        tally.setdefault((winner.source, field), [0, 0])[0] += 1
        for c in others:
            if _agrees(conflict, c, winner):
                continue  # this claim actually matched the resolved truth -- not evidence it was wrong
            tally.setdefault((c.source, field), [0, 0])[1] += 1

    reliability = state["reliability"]
    for (source, field), (wins, losses) in tally.items():
        net = (wins - losses) / (wins + losses)  # in [-1, 1]
        current = reliability.setdefault(source, {}).get(field, NEUTRAL_RELIABILITY)
        reliability[source][field] = min(1.0, max(0.0, current + RELIABILITY_LEARNING_RATE * net))


def decay_reliability(state):
    for fields in state["reliability"].values():
        for field, score in fields.items():
            fields[field] = score + (NEUTRAL_RELIABILITY - score) * RELIABILITY_DECAY_RATE