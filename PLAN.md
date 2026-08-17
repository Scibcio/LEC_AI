# Multi-Source Inventory Reconciliation Agent - Build Plan

Working doc. Check items off as done.

Status: All 8 phases done. R1-R8 satisfied, DoD checklist complete.
Last updated: 2026-08-16

---

## 1. Problem

3+ independent systems (WMS, e-commerce shop, supplier API) each hold their
own version of product truth and drift apart. Most drift is harmless lag;
some is a real error (overselling, stale pricing). The hard part isn't
detecting a mismatch — it's deciding which ones deserve action, which source
to believe when they conflict, and what to do first when the data itself
doesn't settle it.

## 2. Approach

A scheduled agent: pull every registered source each tick → classify each
disagreement as lag or error → resolve conflicts via a stated authority rule
(not majority vote) → emit a ranked action list with pairwise reasoning →
persist state, score source accuracy over time, adjust trust accordingly.

## 3. Requirements (R1-R8)

- [x] R1: ≥3 independent sources
- [x] R2: ≥2 inconsistency types (stock, price)
- [x] R3: Ranked actions with explicit reasoning for the order
- [x] R4: Documented source-of-truth rule (code + README)
- [x] R5: Demo case where the agent's call beats naive majority vote
- [x] R6: State persists across runs
- [x] R7: Confidence thresholds adapt from history
- [x] R8: Runs continuously or on a schedule

## 4. Key design decisions

**Source of truth:** `trust = domain_authority * freshness * learned_reliability`,
computed per source, per field. Resolver = argmax over whatever claims exist
for a (sku, field) — works for any N sources, not just 3.
- domain_authority: per-field competence map (WMS owns qty, shop owns price,
  supplier owns lead time/cost)
- freshness: decays past each source's staleness window
- learned_reliability: running accuracy, persisted, updated on resolution
- Why not majority vote: two stale sources agreeing isn't evidence — it's the
  same bad read counted twice.

**Lag vs error:**
- Stock: `available_to_sell` and `on_hand_qty` are not the same quantity —
  shop's figure is on-hand minus units reserved for open orders, so a gap
  between them is not automatically a problem. The detector adjusts for
  this first: `residual = wms.qty - (shop.qty + shop.reserved)`. LAG if
  the disagreeing record's age is within its own staleness window AND the
  residual is small (~3-5 units, ordinary timing noise) AND its direction
  is benign (residual > 0, i.e. WMS ahead of shop-plus-reserved — the
  expected order-fulfillment lag). ERROR if age is outside the window, the
  residual is large even in the benign direction, or the direction is
  inverted (shop's raw available_to_sell alone exceeds WMS's on-hand — the
  dangerous, overselling-shaped signal, flagged regardless of reserved:
  reservations only make true availability worse, never better, so they
  can't excuse this direction).
- Price: LAG if the disagreeing record's own last-sync timestamp predates
  the winning source's most recent value (it hasn't had a chance to
  observe the change yet) AND its age is within its own window. ERROR if
  its last sync happened after the winning value changed and it still
  shows the old price — a real propagation failure, not lag.

**Ranking:** `priority = severity * confidence * urgency`. Below-threshold
confidence → the action becomes ESCALATE_HUMAN, a real ranked output with its
own reasoning, not a fallback.

**State (state.json):** conflict fingerprints (hash of sku+type only, not the
values, so drift doesn't look "new") with first_seen/last_seen/ticks_seen, and
per-source-per-field reliability (mirrors domain_authority's shape, since a
source can be good at qty but bad at price). No separate confidence-threshold
store: reliability alone is the adaptation mechanism — it feeds back into the
same trust formula, so a chronically-wrong source needs a bigger edge to win
next time. Updated only on ERROR resolutions; LAG isn't evidence anyone was
wrong. Decays back toward neutral (0.5) every tick so a source punished once
doesn't stay punished forever.

**N-source design:** SOURCES is a registry list (`name, loader, authority
map, staleness window`), not named variables. Detectors/resolver operate on
however many claims exist per (sku, field). Adding a source = one registry
entry + one loader, no changes to detect/trust/rank/state. New sources start
at neutral reliability (0.5) so they neither dominate nor get ignored on day
one.

## 5. Build steps

### Phase 1 - scaffolding
- [x] Repo structure, empty modules, .gitignore
- [x] Record shape: sku, qty, price, last_updated, source
- [x] 3 stub sources as JSON (data/)
- [x] SOURCES registry: name, loader, authority map, staleness window

### Phase 2 - ingest and detect
- [x] Loader → common record
- [x] Detectors: stock mismatch, price divergence (N-claim aware)
- [x] Conflict object: sku, type, per-source claims, ages

### Phase 3 - reasoning core
- [x] Freshness scoring, trust calc, lag/error classifier
- [x] Resolver: winner + confidence + written reason
- [x] Majority-vote path (kept only for demo contrast)

### Phase 4 - actions and ranking
- [x] Action types: RESTOCK, PRICE_ADJUST, ALERT, ESCALATE_HUMAN
- [x] Severity/confidence/urgency scoring, ranker with pairwise justification
- [x] Console + JSON output

### Phase 5 - state and learning
- [x] Load/save state.json, fingerprint suppression
- [x] Escalate if unresolved across N ticks
- [x] Reliability update, threshold adaptation, decay to neutral

### Phase 6 - runner
- [x] --once, --loop N, --explain SKU, --reset

### Phase 7 - demo (5 scripted, deterministic ticks)
- [x] T1 clean baseline · T2 lag, correctly ignored · T3 the 50-vs-80 case,
      WMS wins over two stale agreeing sources (R5) · T4 low-confidence price
      divergence → escalate · T5 supplier repeatedly wrong → reliability
      decays, threshold shifts (R7)

### Phase 8 - docs
- [x] README: what/how-to-run, defense of the rule in prose, one worked
      ranking example
- [x] Docstrings on resolver/ranker

## 6. File structure

    LEC_AI/
      PLAN.md / README.md
      agent/
        models.py    record, conflict, action shapes
        sources.py   loaders + SOURCES registry
        detect.py    stock mismatch, price divergence
        trust.py     freshness, authority, reliability, resolver
        rank.py      scoring, ranker, justification
        state.py     load/save, fingerprints, reliability updates
        runner.py    CLI, tick loop
      data/          wms.json, shop.json, supplier.json
      demo/          scenario.py (5 ticks)
      state.json     generated, gitignored

New source = one file under data/ + one SOURCES entry. Nothing else changes.
Stack: Python stdlib only.

## 7. Definition of done

- [x] R1-R8 all checked
- [x] `python -m agent.runner --once` → ranked list with reasons
- [x] `python demo/scenario.py` → all 5 ticks; T3 shows agent vs majority
      vote diverging, T5 shows a threshold moving
- [x] README defends the rule in prose

## 8. Notes worth remembering

- Report confidence, not just use it ("trusted WMS 0.81 vs shop 0.34") —
  makes the agent auditable, not oracular.
- Ranking justification must be relative/pairwise ("above X because..."),
  not bare scores.
- Reliability decays back toward 0.5 over time — a source shouldn't stay
  punished forever.
- Prove N-source extensibility live if time allows: add a 4th source via the
  registry only, nothing else touched.
