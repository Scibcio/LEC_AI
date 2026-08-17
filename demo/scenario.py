"""
Scripted, deterministic 5-tick demo (PLAN.md Phase 7). Builds Records
in-memory instead of reading data/*.json, and uses its own local state dict
instead of state.json, so running this never touches the real dataset or
persisted state. One state dict is threaded across all 5 ticks so the
reliability learning in T5 is genuinely cumulative, same as real usage.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import state as st
from agent.detect import detect_all
from agent.models import Record
from agent.rank import explain_ranking, print_console_table, rank_actions
from agent.sources import SOURCES
from agent.trust import classify, majority_vote, resolve

START = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)


def rec(sku, source, now, minutes_ago, qty=None, price=None, reserved=None, lead_time_days=None):
    return Record(
        sku=sku,
        source=source,
        qty=qty,
        price=price,
        last_updated=now - timedelta(minutes=minutes_ago),
        reserved=reserved,
        lead_time_days=lead_time_days,
    )


def run_tick(title, now, records, state):
    print(f"\n=== {title} ===")
    conflicts = detect_all(records)
    if not conflicts:
        print("no conflicts detected -- all sources agree")
        return conflicts

    resolutions = []
    for c in conflicts:
        winner, _, _ = resolve(c, now, state["reliability"])
        verdict = classify(c, winner, now)
        resolutions.append((c, winner, verdict))
        st.record_conflict(state, c, now)

    actions = rank_actions(conflicts, now, state["reliability"])
    st.update_reliability(state, resolutions)
    st.decay_reliability(state)

    if actions:
        print_console_table(actions)
        print(explain_ranking(actions))
    else:
        print("conflict(s) detected but classified as lag -- no action")
    return conflicts


def tick_1(state):
    now = START
    records = [
        rec("SKU-D1", "wms", now, 2, qty=100),
        rec("SKU-D1", "shop", now, 5, qty=98, reserved=2, price=10.00),
        rec("SKU-D1", "supplier", now, 10, qty=101, price=10.00, lead_time_days=3),
    ]
    run_tick("T1: clean baseline", now, records, state)


def tick_2(state):
    now = START + timedelta(hours=1)
    records = [
        rec("SKU-D2", "wms", now, 2, qty=50),
        rec("SKU-D2", "shop", now, 50, qty=49, reserved=1, price=20.00),
        rec("SKU-D2", "supplier", now, 10, qty=51, price=22.00, lead_time_days=4),
    ]
    print("\n--- price changed from 20.00 to 22.00 at supplier 10 min ago; shop's last")
    print("    sync (50 min ago) predates that change -- it hasn't had a chance to see it")
    run_tick("T2: price lag, correctly not actioned", now, records, state)


def tick_3(state):
    now = START + timedelta(hours=2)
    records = [
        rec("SKU-104", "wms", now, 2, qty=50),
        rec("SKU-104", "shop", now, 150, qty=80, reserved=0, price=24.99),
        rec("SKU-104", "supplier", now, 720, qty=80, price=24.99, lead_time_days=5),
    ]
    conflicts = run_tick("T3: the 50-vs-80 case (R5)", now, records, state)
    c = next(c for c in conflicts if c.type == "stock")
    winner, _, _ = resolve(c, now, state["reliability"])
    mv = majority_vote(c)
    print(f"\nnaive majority vote says {mv} (2 sources agree); agent trusts wms's {winner.qty} instead")
    print(f"agent differs from majority vote: {mv != winner.qty}")


def tick_4(state):
    now = START + timedelta(hours=3)
    records = [
        rec("SKU-D4", "wms", now, 2, qty=75),
        rec("SKU-D4", "shop", now, 2, qty=75, reserved=0, price=15.00),
        rec("SKU-D4", "supplier", now, 2520, qty=76, price=15.60, lead_time_days=4),
    ]
    run_tick("T4: low-confidence price divergence -> escalate", now, records, state)


def tick_5(state):
    now = START + timedelta(hours=4)
    print("\n--- simulating supplier being badly wrong on stock 3 times in a row ---")
    for i in range(3):
        drift_records = [
            rec("SKU-D5A", "wms", now, 2, qty=100),
            rec("SKU-D5A", "shop", now, 5, qty=99, reserved=1),
            rec("SKU-D5A", "supplier", now, 10, qty=150),
        ]
        conflicts = detect_all(drift_records)
        c = conflicts[0]
        winner, _, _ = resolve(c, now, state["reliability"])
        verdict = classify(c, winner, now)
        st.update_reliability(state, [(c, winner, verdict)])
        st.decay_reliability(state)
        print(f"round {i + 1}: winner={winner.source}, supplier qty reliability now "
              f"{state['reliability']['supplier']['qty']:.3f}")

    print("\n--- now a close call between only wms and supplier (shop doesn't cover this sku) ---")
    proof_records = [
        rec("SKU-D5B", "wms", now, 75, qty=40),
        rec("SKU-D5B", "supplier", now, 5, qty=55),
    ]
    conflicts = detect_all(proof_records)
    c = conflicts[0]

    neutral = {"wms": {"qty": 0.5}, "supplier": {"qty": 0.5}}
    w_before, conf_before, _ = resolve(c, now, neutral)
    w_after, conf_after, _ = resolve(c, now, state["reliability"])
    print(f"with neutral reliability      : winner={w_before.source} (confidence {conf_before:.3f})")
    print(f"with supplier's learned history: winner={w_after.source} (confidence {conf_after:.3f})")
    print(f"outcome flipped due to reliability history: {w_before.source != w_after.source}")

    run_tick("T5: supplier repeatedly wrong -- full tick", now, proof_records, state)


def main():
    # a fresh local state -- never touches the real state.json
    state = {
        "conflicts": {},
        "reliability": {s["name"]: {f: 0.5 for f in s["domain_authority"]} for s in SOURCES},
    }
    tick_1(state)
    tick_2(state)
    tick_3(state)
    tick_4(state)
    tick_5(state)


if __name__ == "__main__":
    main()