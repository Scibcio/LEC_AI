"""
CLI entrypoint: wires sources -> detect -> trust -> rank -> state into one
tick, plus a loop mode, a per-SKU decision trace, and a state reset. See
PLAN.md phases 1-5 for what each module actually does.
"""

import argparse
import sys
import time
from datetime import datetime, timezone

from . import state as st
from .detect import detect_all
from .models import field_for
from .rank import explain_ranking, print_console_table, rank_actions, to_json
from .sources import REPO_ROOT, load_all, snapshot_as_of
from .trust import NEUTRAL_RELIABILITY, classify, freshness, majority_vote, resolve, trust

REPORT_FILE = REPO_ROOT / "report.json"


def _now(now_override):
    if now_override:
        return datetime.fromisoformat(now_override.replace("Z", "+00:00"))
    # as_of freezes the clock for reproducibility; live feeds get real time
    return snapshot_as_of() or datetime.now(timezone.utc)


def run_tick(now_override=None):
    now = _now(now_override)
    records = load_all()
    conflicts = detect_all(records)
    data = st.load_state()
    reliability = data["reliability"]  # frozen for this tick

    # drop resolved conflicts so ticks_seen resets if they reappear
    live = {st.fingerprint(c) for c in conflicts}
    for fp in set(data["conflicts"]) - live:
        data["conflicts"].pop(fp)

    resolutions = []
    ticks_seen = {}
    for conflict in conflicts:
        winner, _, _ = resolve(conflict, now, reliability)
        verdict = classify(conflict, winner, now)
        resolutions.append((conflict, winner, verdict))
        entry = st.record_conflict(data, conflict, now)
        ticks_seen[st.fingerprint(conflict)] = entry["ticks_seen"]

    actions = rank_actions(conflicts, now, reliability, ticks_seen)

    st.update_reliability(data, resolutions)
    st.decay_reliability(data)
    st.save_state(data)

    print_console_table(actions)
    if actions:
        print()
        print(explain_ranking(actions))
    with open(REPORT_FILE, "w") as f:
        f.write(to_json(actions))
    return actions


def explain_sku(sku, now_override=None):
    now = _now(now_override)
    records = load_all()
    conflicts = {c.sku: c for c in detect_all(records)}
    conflict = conflicts.get(sku)
    if conflict is None:
        print(f"{sku}: no conflict detected -- all sources agree (or sku not in the dataset)")
        return

    data = st.load_state()
    field = field_for(conflict)

    print(f"{sku} ({conflict.type} conflict, comparing '{field}')")
    print(f"{'source':<10} {'qty':>6} {'price':>8} {'age(min)':>9} {'fresh':>6} {'trust':>6}")
    for c in conflict.claims:
        age = (now - c.last_updated).total_seconds() / 60
        reliability = data["reliability"].get(c.source, {}).get(field, NEUTRAL_RELIABILITY)
        t = trust(c, field, now, reliability)
        print(f"{c.source:<10} {str(c.qty):>6} {str(c.price):>8} {age:>9.1f} {freshness(c, now):>6.2f} {t:>6.2f}")

    winner, confidence, reason = resolve(conflict, now, data["reliability"])
    verdict = classify(conflict, winner, now)
    print()
    print(f"resolver: {reason}")
    print(f"confidence: {confidence:.3f}  verdict: {verdict}")

    mv = majority_vote(conflict)
    agrees = "agrees with" if mv == getattr(winner, field) else "DIFFERS from"
    print(f"naive majority vote: {mv} ({agrees} resolver)")

    actions = rank_actions([conflict], now, data["reliability"])
    if actions:
        a = actions[0]
        print(f"action: [{a.type}] priority={a.priority:.3f} -- {a.reason}")
    else:
        print("action: none (classified as lag, no action needed)")


def main():
    parser = argparse.ArgumentParser(description="Multi-source inventory reconciliation agent")
    parser.add_argument("--once", action="store_true", help="run a single tick (also the default)")
    parser.add_argument("--loop", type=int, metavar="SECONDS", help="run every N seconds until interrupted")
    parser.add_argument("--explain", metavar="SKU", help="full decision trace for one SKU")
    parser.add_argument("--reset", action="store_true", help="wipe state.json for a clean demo")
    parser.add_argument("--now", help="override 'now' as an ISO timestamp -- for demos/tests, not live use")
    args = parser.parse_args()

    if args.reset:
        if st.STATE_FILE.exists():
            st.STATE_FILE.unlink()
        print("state reset")
        return

    if args.explain:
        explain_sku(args.explain, args.now)
        return

    if args.loop:
        try:
            while True:
                run_tick(args.now)
                sys.stdout.flush()  # avoid buffering when piped
                time.sleep(args.loop)
        except KeyboardInterrupt:
            pass
        return

    run_tick(args.now)


if __name__ == "__main__":
    main()