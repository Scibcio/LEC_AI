# LEC_AI

Multi-source inventory reconciliation agent (internship project). Three
independent sources — a warehouse system, an e-commerce shop, and a supplier
feed — disagree about the same products constantly. The agent decides
whether each disagreement is real or just lag, which source to trust, and
what to do about it. Full design and reasoning rules: [PLAN.md](PLAN.md).

## How to run

    python -m agent.runner --once               # single tick against data/*.json
    python -m agent.runner --loop 300           # tick every 5 minutes on a schedule
    python -m agent.runner --explain SKU-104    # full decision trace for one SKU
    python -m agent.runner --reset              # wipe state.json for a clean run
    python demo/scenario.py                     # scripted 5-tick story (R5, R7 made visible)

Stdlib only, no install step. Paths are anchored to the repo internally, so
it runs from any directory.

The fixtures in `data/` each declare an `as_of` timestamp — the moment the
snapshot was captured — and the agent reasons as of that moment, so the demo
reads identically no matter what day you run it. A live feed omits `as_of`
and gets real wall-clock time instead. Without this, every record would age
past its staleness window overnight and the freshness term would decide
every conflict by which source has the longest sync interval.

## Why this rule, not majority vote or a fixed hierarchy

The agent picks a winner using `trust = domain_authority × freshness ×
learned_reliability`, computed per source, per field — not majority vote,
not "always trust WMS."

**Not majority vote:** in SKU-104, shop and supplier both claim 80 units
while WMS says 50. Two sources agreeing looks like evidence, but they're both
reading a stale copy of the same reality, not two independent verifications.
WMS is the only source that physically counts stock — it's the warehouse
system, the reference authority for quantity. The other two are reading
a shared upstream. One observation from two sources is still one observation.
The agent trusts WMS, one against two. Naive majority vote would side with
the crowd and miss the overselling risk (`python -m agent.runner --explain
SKU-104` shows this directly: naive majority says 80, the agent trusts 50).

**Not a fixed hierarchy:** in SKU-110, WMS hasn't synced in 5 hours while
shop and supplier independently agree on a fresh number. Here the agent
trusts shop over WMS. Domain authority sets who's *supposed* to know a
fact, but a stale authoritative source can still lose to a fresh
non-authoritative one — that's what the freshness term is for. And
`learned_reliability` covers the third case: a source that's structurally
trustworthy but has been factually wrong lately needs a bigger edge to win
next time, until its track record recovers (`demo/scenario.py`'s T5 proves
this concretely — a close call flips winners once a source has been wrong
three times in a row).

## Worked example: SKU-130 vs SKU-140

Same inconsistency type (stock mismatch), same winning source (WMS), but
ranked very differently:

- **SKU-130** — shop offers 42 units for sale; WMS and supplier agree only
  ~8-12 physically exist. Active overselling: a customer could complete a
  checkout for stock that isn't there, breaking an order already accepted.
  Ranked `RESTOCK`, priority 2.47 — the highest in the dataset.
- **SKU-140** — shop shows 95 available; WMS and supplier agree on ~150.
  Shop is *under*-advertising real stock. No accepted order is at risk,
  worst case is a few missed sales. Ranked `ALERT`, priority 0.45 — about
  5x lower.

Same detector, same resolved truth, deliberately opposite severity —
because breaking a promise already made costs more than one never offered.

## Demo dataset (data/)

10 SKUs, each isolating one thing so its expected outcome is checkable
against the reasoning core directly.

| SKU     | Type  | Verdict  | Situation                                                  | Expected output                             |
|---------|-------|----------|------------------------------------------------------------|---------------------------------------------|
| SKU-100 | —     | CLEAN    | All sources agree                                          | No action                                   |
| SKU-104 | Stock | ERROR    | WMS 50 vs shop/supplier 80, both stale                     | Trust WMS — ALERT (overselling risk)        |
| SKU-105 | Stock | CLEAN    | Shop 194 + 6 reserved = 200, matches WMS exactly           | No action                                   |
| SKU-110 | Stock | ERROR    | WMS stale 5h; shop/supplier agree ~60                      | Trust shop/supplier — ALERT (resync WMS)    |
| SKU-120 | Stock | ESCALATE | 310 / 295 / 320, no clear winner                           | ESCALATE_HUMAN                              |
| SKU-130 | Stock | ERROR    | Shop offers 42; WMS/supplier show ~8-12                    | Trust WMS/supplier — RESTOCK (top severity) |
| SKU-140 | Stock | ERROR    | Shop 95 + 5 reserved = 100; WMS/supplier ~150, 50 short    | Trust WMS/supplier — ALERT (low severity)   |
| SKU-205 | Price | LAG      | Supplier changed price 09:50; shop synced 09:20            | No action yet                               |
| SKU-210 | Price | ERROR    | Supplier changed price 09:00; shop synced 09:35, still old | Trust supplier — PRICE_ADJUST               |
| SKU-215 | Price | ERROR    | Supplier price 5 days stale; shop fresh                    | Trust shop — ALERT (flag supplier feed)     |

Notes:
- SKU-104 is the case where the agent's call differs from naive majority vote (R5).
- SKU-130 vs SKU-140: same inconsistency type, deliberately opposite severity, to prove the ranking isn't accidental.
- SKU-205 vs SKU-210: same symptom (stale price), different verdict (lag vs error) depending on timing.
- Stock comparisons credit shop's `reserved` units before comparing to WMS — a raw gap isn't automatically a problem (PLAN.md 4.2).

Confidence numbers aren't shown in the table above — run
`python -m agent.runner --explain <SKU>` for the real per-source trust
breakdown behind any of these verdicts. All ten match what the running
agent actually produces (`python -m agent.runner --once` reproduces this
exact table against `data/*.json`). Priority numbers quoted in the worked
example assume fresh state — run `python -m agent.runner --reset && python -m
agent.runner --once` to match them. They rise ~8% over the first dozen ticks
as reliability settles, then hold.

## What I'd do next with more time

**Replace corroboration with real ground truth.** Reliability currently learns
only from conflicts where an independent source agrees with the winner. That's
a proxy — it can't catch two sources wrong the same way, e.g. both reading a
shared upstream. The fix is a feedback channel: a human confirming a
resolution, or a physical stock count, scored against instead. The hook is the
corroboration test in `update_reliability`; swapping it for an oracle call is a
small change to a function that was written expecting it.

**Fit the thresholds to data instead of asserting them.** `STOCK_GAP_FLOOR`,
`CONFIDENCE_THRESHOLD` and the domain-authority weights are hand-picked to
behave sensibly on ten SKUs. Their relative ordering is defensible and argued
above; the absolute numbers aren't fitted to anything. With real outcome data
they'd be tuned against actual resolution accuracy.

**Lock `state.json`.** Writes are atomic, but the read-modify-write cycle
isn't. Two agents ticking at once both load, both decide, and the second save
discards the first's updates. A lock file or a single-writer guarantee closes it.

**Make freshness comparable across sources.** `freshness` is capped at 1.0, so
everything inside its own staleness window scores identically — a 12-hour-old
supplier record and a 2-minute-old WMS record are both "fresh". That's
deliberate (windows differ hugely per source) but it means freshness only ever
penalises, never rewards. Worth revisiting with real sync-rate data.

**Source health as a first-class signal.** A WMS that hasn't synced in five
hours is currently scored as "nobody's affected yet" and ranks low. In a real
deployment that's a leading indicator and probably deserves its own alert
track, separate from per-SKU reconciliation.
