# Known issues

Things that are wrong, unproven, or unfinished in this build. The agent runs
and produces correct output for the demo dataset — these are the honest gaps
behind that. Roughly ordered by how much they'd matter beyond a demo.

| # | Issue | Where |
|---|-------|-------|
| 1 | Reliability learning relies on corroboration as a proxy for truth | `state.py:85` |
| 2 | `state.json` has no concurrency protection | `state.py:41` |
| 3 | Two whole audit lenses never ran | — |
| 4 | Every threshold is hand-asserted, none derived | `detect.py:6-8`, `rank.py:20-28`, `state.py:18-19` |
| 5 | `--loop` against fixtures ticks a frozen clock | `runner.py:_now` |

---

**1. Reliability learning has no ground truth.** `update_reliability` learns
only from conflicts where an independent second source corroborates the winner
— a strong proxy for correctness, but still a proxy. It cannot catch two
sources wrong the same way (e.g. both reading a shared upstream that's
systematically biased). Fixing it properly needs an external feedback channel
— a human confirmation or a physical count — to score against actual truth.
That's the hook where a real feedback system would plug in: replace the
corroboration test in `update_reliability` with a call to an oracle. For now,
corroboration is the strongest signal available without one.

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

**5. `--loop` against fixtures ticks a frozen clock.** Fixture files declare an
`as_of` timestamp and the agent reasons as of that moment (this is what keeps
the demo reproducible — see README). Under `--loop` that means record *ages*
never advance; only reliability and `ticks_seen` evolve between ticks. So the
loop demonstrates scheduling and persistence, not genuine time-based staleness.
A live feed omitting `as_of` gets real wall-clock time and behaves properly.

---

Not listed: anything already fixed. The audit and testing history for this
build is in the git log / session notes, not here.