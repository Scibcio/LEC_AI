"""
Stdlib unittest, no dependencies: python -m unittest discover -s tests

Two jobs. First, turn the README's demo table from a claim into a contract --
every row asserted against what the agent actually emits. Second, pin the
regressions found in review, each of which produced plausible-looking output
right up until it didn't.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import state as st
from agent.detect import detect_all
from agent.models import Record
from agent.rank import ESCALATE_HUMAN, _is_tie, rank_actions
from agent.sources import SOURCES, load_all, snapshot_as_of
from agent.trust import majority_vote, resolve

NOW = snapshot_as_of()


def fresh_state():
    return {
        "conflicts": {},
        "reliability": {s["name"]: {f: 0.5 for f in s["domain_authority"]} for s in SOURCES},
    }


def actions_by_sku(state=None, ticks_seen=None):
    state = state or fresh_state()
    conflicts = detect_all(load_all())
    return {a.sku: a for a in rank_actions(conflicts, NOW, state["reliability"], ticks_seen)}


def rec(sku, source, minutes_ago, qty=None, price=None, reserved=None):
    return Record(
        sku=sku, source=source, qty=qty, price=price,
        last_updated=NOW - timedelta(minutes=minutes_ago), reserved=reserved,
    )


class TestDemoTable(unittest.TestCase):
    """One assertion per README row. If a row and the code disagree, one of
    them is a lie, and the table is the thing a reviewer reads first."""

    EXPECTED = {
        "SKU-100": None,              # all sources agree
        "SKU-104": "ALERT",           # trust wms over two agreeing stale sources
        "SKU-105": None,              # 194 + 6 reserved == 200, reconciles exactly
        "SKU-110": "ALERT",           # wms stale 5h, shop/supplier agree
        "SKU-120": "ESCALATE_HUMAN",  # no clear winner
        "SKU-130": "RESTOCK",         # 8 on hand, below the reorder floor
        "SKU-140": "ALERT",           # under-advertising, no broken orders
        "SKU-205": None,              # price lag, shop hasn't had a chance to sync
        "SKU-210": "PRICE_ADJUST",    # price error, shop had the chance and missed it
        "SKU-215": "ALERT",           # supplier feed 5 days stale
    }

    def test_every_row(self):
        actual = actions_by_sku()
        for sku, expected_type in self.EXPECTED.items():
            with self.subTest(sku=sku):
                got = actual.get(sku)
                self.assertEqual(expected_type, got.type if got else None)

    def test_overselling_outranks_underselling(self):
        a = actions_by_sku()
        self.assertGreater(a["SKU-130"].priority, a["SKU-140"].priority)

    def test_agent_beats_majority_vote_on_sku_104(self):
        conflict = next(c for c in detect_all(load_all()) if c.sku == "SKU-104" and c.type == "stock")
        winner, _, _ = resolve(conflict, NOW, fresh_state()["reliability"])
        self.assertEqual(50, winner.qty)
        self.assertEqual(80, majority_vote(conflict))  # the crowd, and the crowd is wrong


class TestEscalationIsForAmbiguity(unittest.TestCase):
    """Regression: escalating on age alone handed the whole queue to a human
    by tick 3, since a reconciliation backlog is unresolved by definition."""

    def test_persistent_but_confident_conflicts_do_not_escalate(self):
        ticks = {f"{sku}:stock": 99 for sku in ("SKU-130", "SKU-104", "SKU-140")}
        actions = actions_by_sku(ticks_seen=ticks)
        for sku in ("SKU-130", "SKU-104", "SKU-140"):
            with self.subTest(sku=sku):
                self.assertNotEqual(ESCALATE_HUMAN, actions[sku].type)

    def test_persistent_and_unconfident_does_escalate(self):
        actions = actions_by_sku(ticks_seen={"SKU-110:stock": 99})
        self.assertEqual(ESCALATE_HUMAN, actions["SKU-110"].type)

    def test_queue_never_becomes_all_escalation(self):
        state = fresh_state()
        for _ in range(10):
            conflicts = detect_all(load_all())
            resolutions = [(c, resolve(c, NOW, state["reliability"])[0], "error") for c in conflicts]
            ticks = {st.fingerprint(c): 99 for c in conflicts}
            actions = rank_actions(conflicts, NOW, state["reliability"], ticks)
            st.update_reliability(state, resolutions)
            st.decay_reliability(state)
        escalated = sum(a.type == ESCALATE_HUMAN for a in actions)
        self.assertLess(escalated, len(actions), "every action escalated -- the valve is stuck open")


class TestReliabilityConverges(unittest.TestCase):
    """Regression: unbatched, uncorroborated learning drove a source to 0.010
    within 7 ticks purely by losing to the formula that picked against it."""

    def run_ticks(self, n):
        state = fresh_state()
        for _ in range(n):
            conflicts = detect_all(load_all())
            resolutions = [(c, resolve(c, NOW, state["reliability"])[0], "error") for c in conflicts]
            st.update_reliability(state, resolutions)
            st.decay_reliability(state)
        return state

    def test_no_source_reaches_the_rails(self):
        for name, fields in self.run_ticks(40)["reliability"].items():
            for field, score in fields.items():
                with self.subTest(source=name, field=field):
                    self.assertGreater(score, 0.05, "source scored to death")
                    self.assertLess(score, 0.95, "source scored to infallible")

    def test_uncorroborated_winner_teaches_nothing(self):
        # wms wins on authority; neither other source agrees with it, so the
        # only "evidence" is the formula's own preference -- decline to learn
        state = fresh_state()
        conflict = next(c for c in detect_all(load_all()) if c.sku == "SKU-104" and c.type == "stock")
        winner, _, _ = resolve(conflict, NOW, state["reliability"])
        st.update_reliability(state, [(conflict, winner, "error")])
        self.assertEqual(0.5, state["reliability"]["wms"]["qty"])

    def test_priority_is_stable_across_runs(self):
        early = actions_by_sku(self.run_ticks(1))["SKU-130"].priority
        late = actions_by_sku(self.run_ticks(40))["SKU-130"].priority
        self.assertLess(abs(late - early) / early, 0.25, "priority drifts too far to quote in a README")


class TestCoverageGap(unittest.TestCase):
    """Regression: a sku the reference source has never heard of produced
    silence -- the single most expensive alarm, muted."""

    def test_missing_reference_record_is_flagged_and_ranked_high(self):
        records = [
            rec("GHOST-1", "shop", 5, qty=80, reserved=0, price=24.99),
            rec("GHOST-1", "supplier", 30, qty=80, price=24.99),
        ]
        conflicts = [c for c in detect_all(records) if c.type == "coverage"]
        self.assertEqual(1, len(conflicts))
        action = rank_actions(conflicts, NOW, fresh_state()["reliability"])[0]
        self.assertEqual("coverage", action.category)
        self.assertGreater(action.priority, actions_by_sku()["SKU-130"].priority)

    def test_sku_nobody_reports_stock_for_is_not_a_coverage_gap(self):
        records = [rec("PRICED-ONLY", "shop", 5, price=10.0), rec("PRICED-ONLY", "supplier", 5, price=10.0)]
        self.assertEqual([], [c for c in detect_all(records) if c.type == "coverage"])


class TestStateDurability(unittest.TestCase):
    def setUp(self):
        self._real = st.STATE_FILE
        self._dir = tempfile.TemporaryDirectory()
        st.STATE_FILE = Path(self._dir.name) / "state.json"

    def tearDown(self):
        st.STATE_FILE = self._real
        self._dir.cleanup()

    def test_corrupt_state_recovers_instead_of_crashing(self):
        st.STATE_FILE.write_text("{ not json")
        state = st.load_state()  # must not raise -- under --loop this ended the run
        self.assertEqual({}, state["conflicts"])

    def test_save_is_atomic_and_leaves_no_temp_files(self):
        st.save_state(fresh_state())
        self.assertEqual(0.5, json.loads(st.STATE_FILE.read_text())["reliability"]["wms"]["qty"])
        self.assertEqual([st.STATE_FILE.name], [p.name for p in st.STATE_FILE.parent.iterdir()])


class TestTieReporting(unittest.TestCase):
    """A 0.2% gap presented as a ranking is precision the inputs don't support."""

    def test_near_equal_priorities_are_called_ties(self):
        a, b = actions_by_sku()["SKU-130"], actions_by_sku()["SKU-104"]
        self.assertEqual(_is_tie(a, a), True)
        self.assertEqual(_is_tie(a, b), abs(a.priority - b.priority) / a.priority <= 0.05)


if __name__ == "__main__":
    unittest.main()
