# Known issues

Things that are wrong, unproven, or unfinished in this build. The agent runs
and produces correct output for the demo dataset — these are the honest gaps
behind that. Roughly ordered by how much they'd matter beyond a demo.

| # | Issue | Where |
|---|-------|-------|
| 1 | Reliability learning has no ground truth — it's circular | `state.py:73` |
| 2 | `state.json` has no concurrency protection | `state.py:41` |
| 3 | Two whole audit lenses never ran | — |
| 4 | Every threshold is hand-asserted, none derived | `detect.py:6-8`, `rank.py:20-28`, `state.py:18-19` |
| 5 | `resolve()`/`classify()` computed twice per conflict per tick | `runner.py:46`, `rank.py:89` |
| 6 | `--loop` against fixtures ticks a frozen clock | `runner.py:_now` |

---

**1. Reliability learning is circular.** `update_reliability` rewards whichever
source the trust formula already picked as winner, and penalises the losers.
So the agent is scoring sources against *its own prior belief*, not against
what was actually true. A source the formula is systematically biased toward
gets its reliability pushed up, which biases the formula further. It can't
self-correct, and on the demo data it happens to reinforce the right answers,
which hides the problem. Fixing it properly needs an external feedback channel
— a human (or a later physical count) confirming who was actually right — and
then scoring against *that*. Currently nothing supplies it.

**2. No concurrency protection on `state.json`.** Two agent instances running
at once will silently clobber each other: read-modify-write with no locking,
last writer wins. This isn't theoretical — a stray `--loop` process left
running during testing quietly rewrote state under every verification run for
an hour and produced convincing but wrong output before it was spotted. Needs
file locking or an atomic write-and-rename at minimum.

**3. Two audit lenses never ran.** A three-lens automated audit (correctness /
completeness / cleanliness) was started; only cleanliness finished — the rest
died on an API spend limit, and the verification stage never ran at all. Its
three findings were checked by hand instead. So **correctness and completeness
have had no systematic sweep** beyond manual testing. That's the most likely
place an unknown bug is still hiding. Re-running that audit is the single
highest-value thing left.

**4. Thresholds are asserted, not derived.** `STOCK_GAP_FLOOR = 5`,
`CONFIDENCE_THRESHOLD = 0.03`, `SEVERITY_BASE`, the `domain_authority` weights,
the learning/decay rates — all hand-picked to produce sensible behaviour on ten
SKUs. The *relative ordering* is defensible and argued in the README; the
absolute numbers aren't fitted to anything. On real data they'd need tuning
against actual outcomes. Worth knowing SKU-120's escalate-vs-act verdict has
the thinnest margin in the dataset (confidence 0.0167 vs a 0.03 threshold), so
it's the first result that would flip if the freshness curve changed.

**5. Redundant resolve/classify.** Each conflict is resolved and classified
twice per tick — once in `run_tick`'s loop for the state update, then again
inside `build_action`. Pure functions, same inputs, so the results are
identical; it's wasted work, not a bug. Deliberately left alone: fixing it
means reshaping `rank_actions`/`build_action` signatures and the runner wiring,
which wasn't worth the risk against ~30 extra calls on a 10-SKU demo. It is a
mild drift hazard though — the two call sites must keep passing the same
`reliability`, and nothing enforces that.

**6. `--loop` against fixtures ticks a frozen clock.** Fixture files declare an
`as_of` timestamp and the agent reasons as of that moment (this is what keeps
the demo reproducible — see README). Under `--loop` that means record *ages*
never advance; only reliability and `ticks_seen` evolve between ticks. So the
loop demonstrates scheduling and persistence, not genuine time-based staleness.
A live feed omitting `as_of` gets real wall-clock time and behaves properly.

---

Not listed: anything already fixed. The audit and testing history for this
build is in the git log / session notes, not here.