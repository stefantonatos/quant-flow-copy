# Project context — read this first

**Maintenance rule: whoever works in this repo updates this file before the session
ends.** Add what was learned, what changed, what broke. Correct anything here that turns
out to be wrong — and say plainly that it was wrong, don't quietly delete it. The user
should never have to re-explain this project from scratch.

Last updated: 2026-08-11.

---

## What the user is actually doing

Stefan is a trader, not a developer. Explain things plainly, skip the jargon, and don't
assume familiarity with git or Python. When he says "idk" or "explain me", that is a
signal to step back and give the plain-language version — not to ask more questions.

He was on a 7-day trial of a **closed-source, invite-only TradingView indicator sold at
$340/year**, marketed under the name "Bman Studios". He wanted to know whether it was
worth the money, and to rebuild it if not.

## The core finding

**The paid indicator's engine is a free open-source script.** It is jdehorty's
*"Machine Learning: Lorentzian Classification" v2.0*, published publicly under
**MPL-2.0**. Established first from the settings panel (every default matched, in order:
K=8, RSI 14/1 → WT 10/11 → CCI 20/1 → ADX 20/2 → RSI 9/1, regime −0.1, EMA/SMA 200,
kernel 8/8/lag 2), then confirmed when Stefan located the actual source. It is committed
here as `lorentzian_classification.pine`.

What the seller changed: Max Bars Back 2000 → 300; exposed "Prediction Lookahead" and
"Feature Normalization Length" as inputs; **deleted the `Use Worst Case Estimates`
toggle**; hardcoded the kernel's Regression Level.

What the seller genuinely added: breaker blocks, BPR/overlapping FVGs, ATR TP/SL
brackets, session tightening, breakeven+ offset. Real work, but not $340/yr of engine.

## Verified facts (arithmetic checked, don't re-derive)

- His stats table showed **197 trades, 142 wins, 72.1%, "W/L Ratio 2.58"** on MNQ1! 1-min.
- **"W/L Ratio 2.58" is exactly 142 ÷ 55** — wins over losses. It is *not* a payoff
  ratio. The table states the win rate twice and never reports profit factor, expectancy,
  or net P&L.
- From the chart labels (entry 29666.25 / TP1 29683.50 / TP2 29700.75 / SL 29649.00):
  **TP1 = exactly 1.00R, TP2 = exactly 2.00R.** So breakeven is 50% and the claim implies
  ~+0.44R per trade.
- Upstream's own tooltip on that table: *"This should NOT replace backtesting and should
  be used for calibration purposes only."* The seller sells that number as the product.
- `Use Worst Case Estimates` defaults to **false**, meaning the table scores optimistic
  mid-bar entries and does **not** avoid intrabar repainting. The seller removed the
  switch, so the conservative number is unreachable in the paid version.

## Things a previous assistant got WRONG — do not repeat

1. **"The training label looks 4 bars into the future."** False. The line is
   `y_train_series = src[4] < src[0] ? short : src[4] > src[0] ? long : neutral`.
   In Pine, `src[4]` is **4 bars ago**. Both terms are in the past. **There is no
   lookahead in the label.**

2. **What is actually there** is stranger and still unresolved. His comment says the
   model *"specializes specifically in predicting the direction of price action over the
   course of the next 4 bars."* The code looks backward. With `long=1, short=-1`, a
   4-bar **rise** labels **short**. So it finds historical bars resembling now, checks
   what price had just done into them, and does the opposite — a mean-reversion
   classifier documented as a forward trend predictor. Flag the uncertainty; don't assert
   it's a bug.

3. **"`engine.py:247` raises `UnboundLocalError`."** False — the close branch is guarded
   by `position != 0`, only true after the open branch assigned it. Fragile, not broken.

4. **"kNN costs ~1.4s per 40k bars."** Wrong by ~4x; that benchmark strided the distance
   loop. Real: **0.137 ms/bar → 5.5s per 40k bars.** Also confirmed the `i % 4 != 0` gate
   is **not** a speedup (0.137 vs 0.136 ms/bar) — it gates neighbour *admission*, not
   distance *computation*, silently discarding a quarter of the training set at fixed
   phase. Probably an artifact of Pine's int-truthiness.

## Environment constraints

- **No market-data egress from this container.** Yahoo, Stooq, Binance, Dukascopy and
  Polygon all return 403 at the org proxy. Do not try to route around it. `data.py`
  already documents the convention: fetchers are written to run on Stefan's **Windows**
  machine, CSV is the transport in.
- **No Pine compiler here.** TradingView is the only one. Expect a round or two of paste
  → error → fix. Two are already fixed (below).
- Stdlib-only Python is a deliberate repo value and it holds — no numpy needed.

## Decisions already made — don't relitigate

- Pine script is for **private use**. Keep jdehorty's MPL-2.0 header and `©jdehorty`
  attribution regardless. If ever published, it must be published open-source.
- Validation must target **MNQ 1-minute**. Data source chosen: **NinjaTrader free
  account** (free CME historical, exportable). Yahoo's `NQ=F` gives only ~7 days, which
  at n≈50 yields a ±13% confidence interval and settles nothing.
- **Validate before extending further** — though the Pine work was done first because it
  was unblocked and the trial was expiring.

## Repo state

Branch `claude/waiting-for-details-56uiem`, draft PR #1.

- `engine.py` — **fixed a serious bug**: fees were charged to `cash` but never recorded
  in `Trade.pnl`, so `win_rate`/`profit_factor` were computed on **gross** P&L. Also
  added intrabar liquidation (a short into a 3× rally previously drove equity to
  −$17,600 and then opened a sign-inverted "LONG"), equity-based sizing, final-bar
  flattening, and inferred annualization (the hardcoded `sqrt(252)` overstated 1-minute
  Sharpe ~37×).
- `Report.summary()` now leads with expectancy and **never prints a win rate without the
  payoff ratio and breakeven win rate beside it**. Preserve this discipline.
- `selftest.py` — 19 tests. The load-bearing one is
  `sum(Trade.pnl) == final - initial`, which would have caught the fee bug.
- `test_engine.py` — 8 tests, from Stefan's own commit. Both suites pass.
- `lorentzian_classification.pine` — jdehorty's source, **keep byte-identical**.
- `pine/addon_smc_brackets.pine` — our added layers.
- `build_pine.py` — concatenates the two into `pine/lorentzian_full.pine` and patches
  `shorttitle` (upstream's is 30 chars; TradingView caps it at 10) and
  `max_boxes_count`/`max_lines_count` (default 50 is too low).
- `fixtures/CAPTURE.md` — trial-capture checklist. **Probably now moot; the trial has
  likely expired.**

## The code — what to hand the user

**`pine/lorentzian_full.pine` is the deliverable.** ~969 lines. That is the single file
Stefan pastes into TradingView → Pine Editor → Save → Add to chart. Everything else is
source for it.

```
lorentzian_classification.pine   jdehorty's script, MPL-2.0. KEEP BYTE-IDENTICAL.
pine/addon_smc_brackets.pine     our added layers. Edit this one.
build_pine.py                    concatenates the two -> pine/lorentzian_full.pine
```

Run `python build_pine.py` after any edit. Never hand-edit `lorentzian_full.pine` — it
is generated and will be overwritten. Never edit jdehorty's file either; `build_pine.py`
applies the two required upstream patches at build time so his source stays clean and
diffable against future releases.

Stefan works from his phone. He has asked for code **as text in the chat** rather than as
a file attachment — do that when he asks, and expect to paste the whole thing.

### What the add-on provides

Breaker blocks (pivot lookback 5, 25 tracked, polarity flips on mitigation) · BPR from
overlapping FVGs · ATR TP/SL brackets anchored to the nearest unmitigated PD array, TP1
at 1R and TP2 at 2R · Asia/London session tightening · a bracket stats table.

Input names and defaults mirror the paid version's panel so settings transfer directly.

Two deliberate departures from the paid version — **preserve both**:
1. Upstream's `Use Worst Case Estimates` toggle is left intact (the seller deleted it).
2. The bracket stats table reports **expectancy in R** and resolves same-bar stop/target
   ambiguity in favour of the **stop**. Optimistic same-bar resolution is exactly what
   manufactures a 72%.

### Pine gotchas already hit — don't reintroduce

- **`shorttitle` must be ≤10 chars.** Upstream's is 30. Patched in `build_pine.py`.
  (`SHORT_TITLE_TOO_LONG`)
- **`var bool x = na` fails** with CE10173 — Pine infers `const bool`. Use `false`.
- **`max_boxes_count` defaults to 50.** The add-on draws 3 boxes per signal, so older
  zones silently vanish. Patched to 500 in `build_pine.py`.
- **Name collisions with upstream globals.** `r` is his relative-weighting input and
  `risk`/`x`/`h`/`lag`/`src`/`size` are all taken. Our locals were renamed `r2` and
  `riskR`. Check any new identifier against the upstream file first.
- **`ta.*` functions must run on every bar.** Calling `ta.lowest`/`ta.highest` inside a
  function that only executes on signal bars corrupts their state. They are hoisted to
  global scope (`lo20`, `hi20`) and passed in.
- **Declare `var` at global scope**, not inside an `if` block.

**Status: not yet confirmed compiling.** Two errors were fixed; the session ended before
Stefan reported a clean compile. Ask him for the current error text and iterate. There is
no Pine compiler in this environment, so paste-and-report is the only loop available.

## Open / next

- Pine still had unresolved compile errors when the session ended. Two were fixed
  (`SHORT_TITLE_TOO_LONG`; `var bool tIsLong = na` → `false`, CE10173). Expect more —
  ask Stefan for the error text and fix iteratively.
- Nothing has been validated yet. No MNQ data has been supplied.
- The planned validation work: a bracket backtester with pessimistic/optimistic intrabar
  fill bounds, a `causality_report()` repaint detector, and controls (coin-flip through
  identical brackets, shuffled labels) to establish what the 1R/2R-plus-breakeven
  geometry yields for free. Full plan in
  `/root/.claude/plans/hold-on-don-t-say-cosmic-hamster.md` (may not persist).

## Honest expectation to keep setting

The most likely outcome is that 72.1% is real *as computed* and simultaneously
meaningless — a high win rate sitting beside negative expectancy, because TP1 at 1R with
a breakeven stop inflates win rate mechanically regardless of signal quality. That is not
fraud; it is an uninformative statistic. Do not let Stefan expect to catch a liar.

Also: MNQ 1-minute ATR runs ~3–6 points, while round-trip commission (~$0.74) plus a tick
of slippage per side (~$1.00) is ~$1.74. **A genuine edge can still be unprofitable at
retail cost structure.** Always report gross and net side by side — "no edge" and "edge
smaller than costs" are different findings.
