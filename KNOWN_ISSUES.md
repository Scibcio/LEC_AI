# Known issues

Things that are wrong, unproven, or unfinished in this build. The agent runs
and produces correct output for the demo dataset — these are the honest gaps
behind that. Roughly ordered by how much they'd matter beyond a demo.

| # | Issue | Where |
|---|-------|-------|
| 1 | Reliability learning relies on corroboration as a proxy for truth | `state.py` `update_reliability()` |
| 2 | `state.json` read-modify-write has no lock (writes are atomic) | `state.py` `load_state()`, `save_state()` |
| 3 | Two whole audit lenses never ran | — |
| 4 | Most thresholds are hand-asserted; one is derived | `rank.py`, `state.py` module constants |
| 5 | `--loop` against fixtures ticks a frozen clock | `runner.py` |

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

**2. `state.json` read-modify-write has no lock.** Writes are atomic (temp file + `os.replace`),
so a reader can never see a half-written file and a crash mid-write can't
corrupt it. What's still unprotected is the read-modify-write cycle: two
agents ticking at once both load, both decide, and the second save silently
discards the first's updates. Not theoretical — a stray `--loop` process left
running during testing rewrote state under every verification run for an hour
and produced convincing but wrong output before it was spotted. Needs a lock
file or a single-writer guarantee.

**3. Two audit lenses never ran.** A three-lens automated audit (correctness /
completeness / cleanliness) was started; only cleanliness finished — the rest
died on an API spend limit, and the verification stage never ran at all. Its
three findings were checked by hand instead. So **correctness and completeness
have had no systematic sweep** beyond manual testing. That's the most likely
place an unknown bug is still hiding. Re-running that audit is the single
highest-value thing left.

**4. Most thresholds are hand-asserted; one is derived.** Hand-picked to produce
sensible behaviour on ten SKUs: `STOCK_GAP_FLOOR`, `PRICE_SPREAD_THRESHOLD`,
`CONFIDENCE_THRESHOLD`, `PERSISTENCE_CONFIDENCE`, `CONFIDENCE_FLOOR`, `TIE_BAND`,
`SEVERITY` and `URGENCY` weights, the `domain_authority` map. The *relative
ordering* is argued in the README; the absolute numbers aren't fitted to anything.

One exception: `RELIABILITY_DECAY_RATE` is derived, not guessed. It must exceed
`RELIABILITY_LEARNING_RATE / 0.5 = 0.10`, or decay can never balance learning
and scores run to the 0.0/1.0 rails. The original 0.02 was below that floor,
which is exactly what happened: supplier reliability crashed to 0.010 within
seven ticks. Now set to 0.12, the agent's learned scores stay mid-range.

**5. `--loop` against fixtures ticks a frozen clock.** Fixture files declare an
`as_of` timestamp and the agent reasons as of that moment (this is what keeps
the demo reproducible — see README). Under `--loop` that means record *ages*
never advance; only reliability and `ticks_seen` evolve between ticks. So the
loop demonstrates scheduling and persistence, not genuine time-based staleness.
A live feed omitting `as_of` gets real wall-clock time and behaves properly.

---

Not listed: anything already fixed. The audit and testing history for this
build is in the git log / session notes, not here.