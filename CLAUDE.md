# Project context — read this first

**Maintenance rule: whoever works in this repo updates this file before the session
ends.** Add what was learned, what changed, what broke. Correct anything here that turns
out to be wrong — and say plainly that it was wrong, don't quietly delete it. The user
should never have to re-explain this project from scratch.

Last updated: 2026-08-22 (FIRST REAL VALIDATION: 15.4 years of real NAS100 hourly data,
via public GitHub repos + Dukascopy mobile export. vwapfade results and the session-anchor
caveat that flips their sign are in the "first real validation" section below). Also this
day: the new `vwapfade` strategy and a real session-anchored `vwap_session()` in engine.py
alongside the old rolling-window `vwap_rolling()`.

---

## Update from the session that merged this into master

This file was written by a session running in a **sandboxed cloud container** (branch
`claude/waiting-for-details-56uiem`, no network egress). A **separate, later session**
ran directly on Stefan's own Windows machine (`C:\Users\Evi\repos\quant-flow-copy`) in
parallel, diverged from the same commit, and did unrelated Python-side work. Both were
merged into `master` with no conflicts (only `engine.py` overlapped, and the two sessions'
changes landed in different regions of it — a clean auto-merge).

**Correction to the claim below:** "No market-data egress from this container" was true
for that sandboxed session only. It is **not** a repo-wide constraint — Stefan's own
machine has normal internet access; `data.py`'s Yahoo/Stooq/Binance fetchers have been
run successfully from there (e.g. `GC=F` gold futures, 252 daily bars). Don't assume no
network without checking which environment you're actually in.

**What the parallel session added (now in `master`, independent of the Pine work above):**
- `strategies.py`: a **Python port** of jdehorty's KNN/Lorentzian-distance classifier
  itself (not just the reference `.pine` file) — `LorentzianClassification`, registry key
  `lorentzian`. Runs through the normal `engine.run()` backtest loop, `compare` screener,
  `--walkforward`, `--montecarlo`, same as every other strategy. This is a faithful
  best-effort translation of the ML core (KNN search, regime/volatility filters, kernel
  regression trend filter, strict 4-bar exits) — approximate on the exact normalization
  constants for WT/CCI (documented as such at the class).
  **⚠️ The training label in that port was SIGN-INVERTED and has since been fixed.**
  It shipped as `labels[i] = 1 if closes[i-4] < closes[i] else -1`, described in this
  file as "exact … confirmed against upstream". It was not. Upstream
  (`.pine:335`, with `long=1 / short=-1` at `.pine:291`) labels a 4-bar **rise** as
  **short (−1)**; the port labelled it **+1**. Nothing downstream compensated — both
  go long on `prediction_sum > 0` — so the port traded the exact **mirror image** of
  the indicator it replicates. Any backtest run against the pre-fix port is void.
  Locked by `TestLorentzianParity` in `selftest.py`; the port now exposes `self.labels`
  so the assertion is direct.
- `strategies.py`: `BollingerRSI` (`bollrsi`) — long when close < lower band AND RSI
  oversold, exit on RSI overbought or close > upper band. Tested on sample data and on
  `GC=F` (gold futures via Yahoo, the free proxy for gold CFD price — no free source
  quotes actual CFD prices). Very few trades on trending gold by design (two rare
  conditions stacked); not yet a validated edge.
- `opt.py` + `run.py optimize <key>`: grid-search optimizer. Small hand-picked param grid
  per strategy, scores on a 70/30 train slice, re-checks top candidates on the held-out
  test slice. Reuses `engine.run()`/`Report` as-is, no new backtest path.
- Found and fixed a second instance of the `ema()` NaN-seed-poisoning bug class (same
  class as the pre-existing `atr()` fix from commit 843e444) inside a new `wavetrend()`
  indicator it added for the KNN port's WaveTrend feature.
- `test_engine.py` grew from 8 to 11 tests (regression tests for the above); all pass
  alongside `selftest.py`'s 19 (unrelated, no overlap in what they cover).

**Still true and unchanged by this merge:** everything below about the Pine deliverable,
the paid-version findings, the compile status, and the open/next items. This section is
additive, not a correction to the Pine-side work.

## Update (2026-08-16): brackets.py, two strategies from videos, bracket-aware optimize

Stefan closed the Pine-parity thread himself ("good enough, move on") and moved to
backtesting. Don't reopen the Pine signal-matching work without a new reason — the full
settings diff came back identical on every input and the residual gap was attributed to
`normalize_windowed()`, our own reimplementation of code nobody has ever seen.

**`brackets.py` is new and is now the main execution path for anything with a stop.**
`engine.run()` is close/flat/short only — no stop, target, or intrabar concept — so it
cannot score any strategy whose edge lives in its trade geometry. `brackets.py` walks
high/low bar by bar and always reports **two** numbers side by side: `pessimistic` (the
stop wins same-bar ties) and `optimistic` (the target wins). **Always report both.** The
gap between them is how much of a headline number is fill-order guesswork. A backtest
quoting only the optimistic figure is the exact trick that manufactures a 72% win rate.

Key invariants in there, all test-locked — don't "simplify" any of them away:
- Gaps fill at the bar's **open**, not at the level.
- `_score()` derives R from **actual fill prices**; nothing may assume TP1 == 1R.
- `allow_entry_bar_fill` defaults **False**. Filling on the entry bar is lookahead when
  entry is that bar's close. It is correct only for a limit fill mid-bar, which is why
  `nowick` opts in and nothing else does.
- `partial_at_tp1` defaults **True** (half off at TP1, stop to breakeven+). `nowick` sets
  it False because a fake scale-out scores a win as 0.75R against a 1R loss and quietly
  moves breakeven from 50% to 57%.

**Two strategies built from videos Stefan sent.** Both mechanize a discretionary
description, so both are *a* reading, not *the* strategy — say so when reporting results:
- `sdz` — TradingLab's "Only Trading Strategy You'll Ever Need". Structure (HH/HL) +
  supply/demand zone + a **2.5:1 R:R filter**. Rules were reconstructed from search-index
  summaries; YouTube and every transcript mirror are egress-blocked here.
- `nowick` — @bardfx's "No Wick". Mark a with-trend candle missing its trend-side wick,
  wait for price to retrace to that flat edge, enter **there** (resting limit, not the
  close), 1:1 target. Indicator identified as **xGhozt Wickless Candles**; wickless is
  exact equality, no tolerance to guess.
  **Stefan runs this on 15-minute FOREX.** That matters: on 5-decimal FX an exactly-zero
  wick is much rarer than on tick-sized futures, so the strict default may yield almost no
  setups — `wick_tol_frac` (wick as a fraction of the bar's range) is the knob for that.
  Also note entry is AT the candle's low, so `stop_buffer_atr` **is** the entire risk and
  sets the whole trade geometry. Too tight and the entry bar straddles both stop and
  target; that shows up honestly as a 100% ambiguous bracket. Default raised 0.10 → 0.50.

**`engine.py` gained `pivots()` / `confirmed_pivots()`.** Use `confirmed_pivots()` — it
re-keys each swing by the bar it becomes *knowable* on (`i + right`), so a strategy cannot
consult a pivot before its right-hand bars have closed. `pivots()` alone is a lookahead
trap and its docstring says so.

**`run.py optimize` is now bracket-aware, and this was a real bug.** For `sdz`/`nowick`
every grid parameter (`rr`, `stop_buffer_atr`, `stop_mode`, `min_rr`) only moves
stops/targets/entries — all invisible to `engine.run()`. Scored through it, the optimizer
printed a ranked table in which **every row had the same number**, which reads like a
result and is the optimizer measuring nothing. `opt.is_bracket_strategy()` now routes
those two through `optimize_brackets()`, ranked by expectancy in R. If you add another
bracket strategy, add it to that predicate.

**Test counts: `selftest.py` 66, `test_engine.py` 13.** Two regression anchors worth
knowing, because they prove the new defaults didn't leak: `lorentzian --bracket` must stay
**−0.183 R over 23 trades** and `sdz --bracket` **+0.226 R over 15 trades** on sample data.

**`pine/no_wick.pine` — and this is the one that unblocks the project.** Stefan asked for
No Wick on TradingView, noting the xGhozt indicator's source is locked. **The locked source
is a non-issue**: wickless is exact equality on OHLC (`low == open` on a green candle), so
there is no hidden algorithm to recover — the protected part is the drawing, not the
definition. Don't waste another session trying to obtain it.

The file is a **`strategy()`, not an `indicator()`**, deliberately: TradingView's Strategy
Tester then backtests it against his own 15m forex chart. **That is the first real market
data this project has ever had** — the CSV-export ask that has blocked everything since day
one is sidestepped entirely for any strategy expressible in Pine. Consider the same route
before asking Stefan to export anything again.

It is standalone and hand-written — **not** part of `build_pine.py`, which exists only to
patch jdehorty's file while keeping it byte-identical. Inputs mirror `NoWickRetrace`
one-for-one so the Python and Pine results are comparable. Entry uses
`strategy.entry(..., limit=level)`, a genuine resting order, so TradingView decides the
fill rather than us; the order rests at the nearest live level on the correct side of price
and is cancelled when no level is live. Single exit, no scale-out (matches
`partial_at_tp1=False`). **Not yet compiled** — same paste → error → fix loop as the
Lorentzian file, which took two rounds.

**Pine line-continuation rule, learned the hard way (CE at `161:40`).** Outside brackets,
a wrapped line must be indented by a number of spaces that is **not** a multiple of four —
otherwise Pine reads it as a new block and throws `Syntax error ... "end of line without
line continuation"`. Inside an unclosed bracket, wrapping is unrestricted, which is why the
multi-line `strategy(...)`, `plotshape(...)` and `bgcolor(...)` calls in the same file were
always fine. Safest habit: wrap long expressions in parentheses. Add this to the gotcha
list below.

**Live results so far (2026-08-16, Stefan's own 15m forex chart).** The script compiles and
runs. **Its wickless marks are identical to the xGhozt indicator's, confirmed on-chart** —
that closes the locked-source question empirically; do not reopen it. He reports it as
profitable, but **that number is not yet trustworthy**: commission/slippage were not
confirmed set, trade count is unknown, and at 1:1 the win rate alone says nothing. Ask for
profit factor, net profit and trade count before treating any of it as a result.

**Known live defect, fixed but unverified: `trend_mode="structure"` shorts obvious
uptrends.** With a 5-bar pivot on 15m the structure gate reads only about an hour, so a
routine pullback prints a lower low and lower high and flips it bearish. A `"both"` mode
(structure AND EMA must agree) was added to the Pine file, `strategies.py` and the opt grid.
Default stays `"structure"` so earlier numbers stay reproducible. Stefan had not yet
re-pasted and re-tested when the session ended.

Two things to insist on when the numbers arrive: **set commission and slippage** (at 1:1
with a sub-ATR stop the FX spread is a large share of the risk, and "edge smaller than
costs" is a different finding from "no edge"), and **check how TradingView's broker
emulator resolves a bar containing both the stop and the target** — unverified, and with
this geometry most trades will hit that case. Bar Magnifier is what actually resolves it.

## Update (2026-08-16, later): third video strategy — `asiasweep`

Stefan sent a transcript: mark the Asian session high/low (Leviathan's Market Sessions
indicator, **confirmed UTC from a screenshot**), wait for a sweep of one side, drop to 5m
for a "change in state of delivery", enter on that candle's close, target the **opposite**
Asia level. Built as `AsiaSweepCSD` / key `asiasweep`, plus `pine/asia_sweep.pine`.

**The "3RR" in the transcript is not a rule and the code cannot make it one.** The target
is a fixed price (the other side of the range) and the stop is below the sweep, so each
setup's R:R is whatever that day's geometry gives — the synthetic test fixture yields
1.86R. `min_rr` exists to filter, defaults to **0.0** so nothing is silently dropped.
Don't quietly "fix" this into a 3R target; that would be a different strategy.

**Asked him the three open questions; he answered "idk" to two of them.** Per the rule at
the top of this file, that is the signal to decide and explain rather than push back with
more questions. Decisions, all exposed as parameters: `csd_mode="candle"` (first close back
above the most recent down-close bar's high — the standard ICT reading), stop below the
sweep extreme (**this one he did specify**), `one_per_day=True`, Asia 00:00–08:00 UTC.
**Session hours are now CONFIRMED** from a screenshot of his Leviathan panel: Tokyo
00:00–09:00, London 07:00–16:00, New York 13:00–22:00, Sydney 21:00–06:00 (unticked). His
"Asia" is the **Tokyo** block, so the default is **00:00–09:00 UTC**. The first shipped
default of 00:00–08:00 was **wrong by an hour** — it built a narrower range than his chart
shows, which moves the sweep, the entry and the target. Corrected in `pine/asia_sweep.pine`,
`strategies.py` and the test fixture (which now builds a 9-hour Asia session).

**It overlaps `po3` but is genuinely distinct** — po3 enters on a close back inside the
range and targets a fixed R multiple; this enters on a CSD and targets the opposite level.
`gen.py` routes "asia"+"sweep/csd/delivery" here and leaves "power of 3"/"po3" on po3;
`test_power_of_three_still_routes` guards that.

**Needs intraday bars with UTC timestamps.** On the daily sample data every bar lands in
hour 0, the session logic degenerates, and it produces nothing — expected, not a bug. The
tests build synthetic 5-minute days for this reason. TradingView is where it gets measured.

Test counts now **`selftest.py` 81, `test_engine.py` 13**. `asiasweep` was added to
`opt.is_bracket_strategy()`.

## Update (2026-08-16, later still): iFVG mode — and a lookahead I nearly shipped

Stefan asked for "an AMD with iFVG strategy". **It is not a new strategy.** An inverse-FVG
inversion *is* a change in the state of delivery — delivery flipping from sell-side to
buy-side is the same claim at a different resolution — so it went in as a third
`csd_mode` on `asiasweep` rather than a fourth script. AMD itself is already `po3`.

- `engine.py` gained **`fvgs(bars, min_size)`** — three-bar imbalances, `dir` 1/-1, keyed by
  the bar they become knowable on. No confirmation lag (unlike pivots): all three bars are
  closed, so a gap at bar `i` is usable at `i`.
- `csd_mode="ifvg"` triggers when price closes back through the most recent un-inverted gap
  of the opposite polarity. `ifvg_entry` picks `"close"` (default, on the inverting bar) or
  `"retest"` (wait for price to return to the flipped zone).

**⚠️ The retest path had a lookahead bug that I caught only because I probed the numbers
before writing the test.** The bar whose close inverts a gap necessarily traded *down
through* that gap on its way up, so filling the retest on that same bar books a price from
earlier in the bar — before the close that generated the signal existed — and it is always
the best price of the bar. On the test fixture it turned a genuine **1.86R into a fictional
3.72R**, i.e. it manufactured exactly the "3RR" the video claims. Fixed by recording an
`armed_at` bar and requiring `k > armed_at`. Locked by
`test_retest_cannot_fill_on_the_inversion_bar_itself`. The Pine file carries the same guard
and the same comment. **Do not "simplify" the armed_at check away.**

The honest post-fix picture, worth repeating to Stefan: on a fixture that rallies straight
off the inversion the retest **never fills at all** (0 trades), and on one that pulls back
it fills at 3.75R. That is the real trade-off — a better price you frequently do not get —
not a free upgrade.

**`gen.py` ordering matters and is now test-locked.** The po3 branch matches the bare word
`amd`, so it was swallowing "AMD with iFVG". The asiasweep branch now sits *above* po3;
`test_amd_with_ifvg_routes_here_and_selects_ifvg_mode` and
`test_power_of_three_still_routes` guard both directions.

Test counts now **`selftest.py` 99, `test_engine.py` 13.**

## Update (2026-08-16, last): SMT divergence — `engine.smt_divergence()` + `pine/smt_sweep.pine`

Stefan sent a prop-trader's daily recap (NQ 1-min / 30-sec) and asked for it. Decomposed,
**four of its six confluences were already in the repo** — HTF fair value gap, liquidity
sweep, iFVG, change in state of delivery. Say this to him rather than building a fifth
overlapping script: these video strategies keep resolving to the same handful of
primitives in different arrangements.

**The one genuinely new mechanism is SMT divergence**, now `engine.smt_divergence(bars,
ref_bars, lookback)`: this instrument makes a new high while a correlated one does not, so
the "breakout" was one index reaching for liquidity rather than both markets going up.

**Two correctness properties, both test-locked, both easy to get wrong:**
- **Alignment is by TIMESTAMP, never by index.** Two real feeds differ in bar count
  (session breaks, holidays, gaps); lining them up positionally compares different moments
  and manufactures divergences that never happened. Unmatched bars produce no signal.
- **`request.security(..., lookahead=barmerge.lookahead_off)` is mandatory** in the Pine
  file. The default hands back future data on historical bars, which would make any SMT
  backtest look extraordinary and mean nothing.

**What could NOT be mechanized, and it is the important part.** He picks targets by eye —
some lows are "low resistance", others "high resistance ... protected by this gap". No rule
in the video reproduces that, so `targetMode` substitutes the last confirmed swing low or a
fixed R multiple. Target selection largely determines the R on every trade, so this script
measures *an interpretation*, not his trading. He also cut the example trade early ("around
20 points to secure the day") because he trades to a prop payout rule — so even the winning
example is not the strategy.

**On the source: the back half of that transcript is an affiliate funnel** — a discount code
for funded-account firms, weekly giveaways for people who use it, a Discord. He is paid per
account bought through the code, so the payout screenshots are marketing for the referral
business, not an audited record. That does not make SMT wrong (it is a real, testable idea,
which is why it got built) but attach zero evidential weight to the numbers.

`requireSMT` defaults true but can be switched off, which is the cheap experiment worth
running first: if SMT contributes nothing, trade count barely moves and expectancy does not
improve.

## Update (2026-08-18): `bridge/` — self-hosted TradingView -> MT5 execution

Stefan asked whether we could copy **viewlink.dev** ("execute your TradingView analysis on
MT5"). Yes, and the finding is the same one this whole project started with: **the product
is hosting, not algorithm.** A bridge is a webhook receiver plus an order placer. What the
paid services actually sell is a URL that stays up when your PC does not — a real service,
just not a secret one. Say that plainly rather than implying they are ripping anyone off.

Two halves, talking through a **plain text file** (deliberate: readable in Notepad
mid-run, cancel a command by deleting its line; a socket would buy nothing here):
- `bridge/webhook_server.py` — stdlib-only HTTP receiver, validates and appends commands.
- `mt5/TradingViewBridge.mq5` — polls the queue, places orders via `CTrade`.

**Both halves refuse independently, on purpose — the EA does not trust the server, because
the EA is the half that spends money.** Rails, all defaulting to safe:
- **Dry run is the default in BOTH** (`--live` flag / `DryRun=true` input).
- Shared secret, **minimum 12 chars, enforced at startup**. The webhook URL is effectively
  public.
- Symbol allowlist; two independent lot caps.
- **Stop-direction validation** — a buy with its stop above entry is refused. That one typo
  turns a bracket into an instant loss or an unprotected position.
- **Duplicate suppression by command id** — TradingView re-fires alerts, and without this a
  retry silently doubles the position.
- The EA closes **only positions carrying its own magic number**, never a hand-placed trade.

**⚠️ CORRECTION to the first version of this section: the webhook route is unusable for
Stefan.** TradingView's free (Basic) plan gives **3 price alerts and ZERO technical
alerts**, and anything driven by a Pine script — indicator, strategy, `alert()` — is a
technical alert. So on his account **no script in this repo can fire an alert at all**, and
both alert-based routes are dead. Webhooks are additionally paid-only. This was shipped
before checking the plan tiers; check a platform's free-tier limits before building on top
of them.

**What actually works on free, and it is what viewlink.dev does:** an *indicator* publishes
the levels and a *browser extension* reads them — no alerts anywhere. Stefan supplied this
detail ("chrome extension + tradingview indicator, when price hits entry it executes on
MetaTrader"), and it was the thing that unblocked the design.

- `pine/bridge_levels.pine` — publishes entry/stop/target as **named plots**. TradingView
  prints plot values in the status line, so the extension can read them off the page. **The
  plot titles Entry/Stop/Target are an interface, not decoration** — renaming them breaks
  the extension's read button.
- `bridge/extension/content.js`'s `readLevels()` was reading TradingView's legend by matching
  the WORDS "Entry"/"Stop"/"Target" next to numbers. **Confirmed via a live console screenshot
  this is wrong**: the compact legend renders the indicator's input values and its plotted
  values as one unlabeled run of numbers (`Bridge manual short 0.593... 14 0.5 1 0 0000-0900
  UTC 82  0.593  0.594  0.591 ...`), no title words at all. Fixed by scoping to the row
  containing "bridge" and taking the LAST THREE numbers on it — Entry/Stop/Target are the
  script's last three visible plots (Long/RR are `display.status_line`-only and never show
  here), so position is reliable even though labels are not. Title-word matching is tried
  first and kept as a fallback in case a wider legend does show labels. **This is still
  scraping and still a heuristic** — the popup shows whatever it reads for the user to check,
  never sends it blind, and that discipline should not be relaxed even though the heuristic
  got better.
- `bridge/extension/popup.*` — reads those levels, shows the R:R, flags a wrong-side stop
  before you send, and posts the trade.

**Deliberate departure from what the paid services do, and worth defending if questioned:**
they keep a browser tab watching price and fire a *market* order on touch. This sends a
**pending limit order** instead, so the broker does the waiting — server-side, no browser,
no extension needed at the moment it matters, no browser latency on the fill. Same outcome,
far fewer things that can be off at the wrong time.

`bridge/test_bridge.py` — **21 tests, all about what the validator must REFUSE.** That is
the right shape for this component: it sits between a public URL and a brokerage account.
Verified end to end over real HTTP (one valid order queued, four bad ones rejected with
reasons).

**Do not lose the warning in `bridge/README.md`:** a bridge does not create edge, it removes
the delay between signal and position. Nothing here is validated yet, so switching this on
makes an unmeasured strategy lose money faster and unsupervised. Measure first.

**Still nothing validated.** Every number above is synthetic random-walk sample data
(`data.py`), which has no market structure — smoke tests that the pipeline runs, not
measurements of edge. Both new strategies fire only a handful of trades on it. The real
blocker is unchanged: **no market-data egress from this container**, so Stefan has to
export the data himself. He now needs **15-minute forex** for `nowick` in addition to the
MNQ 1-minute for the Lorentzian work.

## Update (2026-08-19/20): bridge taken live on Stefan's own laptop — money-based sizing,
## broker symbol resolution, and a real limit on what Pine can do

Stefan actually set the bridge up end to end (Chrome extension + MT5 EA + webhook server)
and ran it dry-run against his live EURNZD chart. Several rounds of real debugging, in
order:

- **Extension DOM reading was wrong five different ways before it was right.** Guessing at
  TradingView's legend markup (title-word matching, "last 3 numbers", leaf-node walking)
  each produced a *plausible-looking* wrong number, not an obvious error. What actually
  fixed it: a "Copy diagnostics" button that dumps the real DOM around the indicator so the
  reader could be built against ground truth instead of another guess. It revealed
  TradingView tags every legend value with `data-test-id-value-title="Entry"` etc. — read
  that attribute directly, never the concatenated `.textContent`. **Lesson for next time
  anything scrapes a UI we don't control: get a real DOM/state dump before writing the
  fourth attempt, don't just guess a fourth time.**
- **Position sizing is now risk-based ("$100 per trade"), computed in MT5, not the
  browser.** `bridge/webhook_server.py` accepts `risk` alongside `lots` (requires `sl` when
  used, capped by `--max-risk`, default 200); `mt5/TradingViewBridge.mq5`'s
  `LotsFromRisk()` converts using the symbol's real `SYMBOL_TRADE_TICK_SIZE`/
  `SYMBOL_TRADE_TICK_VALUE`, rounds lots DOWN, and **refuses the trade** (never silently
  trades the broker minimum) if the rounded size would risk more than asked. `MaxLots` is
  now just a backstop (5.0) against a malformed price; `MaxRiskMoney` is the real cap.
- **`ResolveSymbol()` added to the EA** so a TradingView symbol like `EURNZD` matches a
  broker's differently-suffixed one (`EURNZD.raw`, `EURNZDm`) by exact match first, then
  prefix match — needed because no two brokers name symbols the same way.
- **`start_bridge.bat`** — one-click launcher: checks Python is on PATH, checks the MT5
  queue path exists (the classic silent failure this guards against: the server happily
  queues into a folder MT5 never reads), opens the chart in Brave, starts MT5, starts the
  server. `SYMBOLS` defaults to empty so the bridge trades whatever chart Stefan has open,
  not just EURNZD.
- **Stale-code confusion cost real debugging time.** Several "still broken" reports were
  Stefan testing an old unpacked-extension folder or a server/EA that was never restarted
  after an update. Fixed by putting the version number in the extension's visible name/
  popup header, and by always calling out explicitly which side (browser / server / EA)
  needs restarting after a given code change — "the extension changed" and "the server
  changed" are different instructions.

**Explicit UX correction — do not reintroduce.** `input.price(..., confirm=true)` on the
three trade-level inputs was briefly changed to `confirm=false` (thinking a settings-dialog
edit would be more convenient than clicking on the chart). Stefan's answer: *"no bro. i
want the clicking thing. its good. i just dont want to have to delete and re place the
indicator to get that function. i dont want to type values into the box. it's not
happening."* Reverted to `confirm=true`. Click-to-place on the chart is the feature, not a
rough edge to smooth over.

**That correction led straight into the actual limit of the platform, now confirmed twice
over — once from Stefan's own Settings-dialog screenshot, once from TradingView's own
docs/support pages: a Pine script cannot reopen that click-through prompt, and cannot move
an already-placed `input.price` point from code. The prompt fires exactly once, when the
indicator is added.** So "give me a button to reset the indicator so I can re-click" has no
scripted answer — there is no button Pine can draw that does this. The only click-only
(no typing) way to move a level once placed is: click the indicator's **name** in the chart
legend to select it (this reveals its draggable handles) → drag each handle to the new
price. That already requires no typing and no delete/re-add, so it satisfies both of
Stefan's constraints — it just isn't a dedicated "reset" affordance, because TradingView
doesn't expose one to scripts. Said this to him plainly rather than shipping a sixth guess.

**This also means the earlier "snap-to-symbol" fix (below) was only a partial fix, and
that's now stated honestly in the code comment.** `manualEntry`/`manualStop`/`manualTgt`
falling back to a `close`-anchored value when the stored price doesn't fit the current
symbol only fixes what gets *published* (the plotted Entry/Stop/Target lines and the
box the extension reads) — it does **not** move the actual draggable `input.price` handles,
because Pine cannot write back to its own inputs. On a big cross-symbol jump (EURNZD →
XAUUSD) the published levels correctly snap to something visible near current price, but
the drag handles themselves are still sitting at the old price and off-screen. There is no
further Pine-side fix available for that half of the problem — it is a genuine platform
limitation, not a bug to keep chasing.

`bridge/test_bridge.py` is now **26 tests**, all still passing (`TestRiskSizing` added:
risk-without-lots accepted, risk-without-a-stop rejected, risk-above-cap rejected, etc.).

**Still not validated with real money and still shouldn't be** — see the standing warning
above. Dry-run only; nothing here has traded live.

## Update (2026-08-22): new strategy `vwapfade` — long-only VWAP-ATR fade

Stefan asked for exactly this: "vwap. only long price when it's 2 ATR below vwap and
take profit at the vwap. only longs. no shorts." Built as `VWAPATRFade` in
`strategies.py`, registry key **`vwapfade`** (distinct from the existing `vwap` key,
which is the older `VWAPReversion` trend-following strategy — unrelated, don't confuse
the two).

**Added `engine.vwap_session()` rather than reusing `vwap_rolling()`.** The existing
`vwap_rolling(bars, n)` is a fixed N-bar window — an explicitly-documented approximation
for data with no real session to anchor to. But "VWAP" on an actual chart (including
TradingView's built-in `ta.vwap()`, which the Pine output below calls directly) means the
**session-anchored** cumulative version, reset each day. Using the rolling one here would
have silently disagreed with what Stefan sees plotted on his own chart — exactly the
"our own reimplementation nobody verified" trap this project has been burned by before
(see the Lorentzian `normalize_windowed()` saga above). `vwap_session()` resets at every
new UTC calendar day; bars with no real volume (common on forex feeds) fall back to equal
weighting rather than NaN.

**No stop-loss, on purpose — that's what was asked for, not an oversight.** The exit is
"reaches VWAP," full stop; there's no ATR-multiple or structural stop parameter. Flagged
this to Stefan directly since an unbounded fade against a trend that never reverts is a
real drawdown risk, but per repo practice (see the "don't add features beyond what the
task requires" rule) nothing was added he didn't ask for. If he wants a safety stop later,
it's a small addition, not a redesign.

**Needs intraday bars, like `orb`/`po3`/`asiasweep`.** On the daily `sample` data each
bar *is* its own session, so `vwap_session()` degenerates to that bar's own typical price
and the entry condition (close 2 ATR *below* a value derived from that same bar) almost
never fires — confirmed: 0 trades on `python run.py "vwap fade" --data sample`. This is
expected, documented in the strategy's own `description` and in `gen.py`'s explanation
text, not a bug. Verified the actual bar loop works by hand-building 40 days of synthetic
hourly bars with a real intraday dip — 39 trades, correct long-only entries/exits, VWAP
and 2xATR band checked directly against the fill bars.

`gen.py` routes "vwap" + ("atr" or "below") to this new strategy (checked *before* the
older generic "vwap" branch, same ordering discipline used for asiasweep/po3/ifvg above)
and extracts an explicit multiplier like "3 atr" if given, defaulting to 2.0. `opt.py` got
a grid (`entry_atr: [1.0..3.0]`, `atr_n: [10,14,20]`); it runs through plain `engine.run()`
posture logic, not `brackets.py` — the exit target is VWAP itself, which moves every bar,
and brackets.py's model assumes a fixed price per trade, so it doesn't fit that shape.

Test counts now **`selftest.py` 109, `test_engine.py` 13**.

## Update (2026-08-22, later): THE FIRST REAL VALIDATION IN THIS PROJECT'S HISTORY

**The "no market data" blocker is broken.** Two routes opened on the same day:

1. **Stefan exported real data from his phone.** Dukascopy's web export works on mobile
   with no login: `fixtures/data/USATECH_1H_2025-01.csv`, Nasdaq-100 CFD, 1-hour, Jan 2025.
   The web UI caps a 1-hour download at ~1 month, so more months = more downloads.
2. **`raw.githubusercontent.com` and the git proxy ARE reachable from this container** even
   though every market-data host (Yahoo, Stooq, Binance, Dukascopy, Frankfurter,
   AlphaVantage, TwelveData, FMP, Tiingo) still 403s. **Public GitHub repos are therefore a
   working data channel.** This was never tried before. `FutureSharks/financial-data`
   (GPL-3) carries NAS100 1-minute bars 2005-2020 from Oanda; `fetch_nas100.py` rebuilds
   them into **92,506 hourly bars over 15.4 years**.

**Recent intraday index data is NOT on GitHub.** Searched hard: 2021-2026 intraday NAS100
is the commercially valuable window and nobody dumps it. The recent out-of-sample has to
keep coming from Dukascopy exports. Don't spend another session searching for it.

### Three bugs the real data exposed immediately, all fixed

- **`load_csv()` returned NaN for every row of a real Dukascopy export.** It names the time
  column after the TIMEZONE (`Etc/UTC`), so no fixed list of column names can ever match.
  Now falls back to the first column. The pre-existing loud NaN warning is what caught this
  — without it the strategies would have silently reported "no setups".
- **`vwap_session()` reset at UTC midnight**, which for an index CFD trading 23:00->21:00
  fires an HOUR INTO the session. Added `anchor_hour`. This is not cosmetic: see below.
- **No way to load multi-file exports.** `load_csv_many()` / `load_path()` stitch a
  directory or glob, deduped by timestamp and sorted. `run.py --data <dir|glob|file>`.
  Deduping is load-bearing — a duplicated bar is a second chance to trade one moment.

### `vwapfade` results on 15.4 years of real NAS100 hourly

**The headline is genuinely good and the caveat is genuinely serious. Report both.**

At `anchor_hour=13` (US cash open), fee 1bp, `entry_atr=2.0`:
1,290 trades · **+129.5%** · expectancy +10.04/trade · win 62.1% · PF 1.23 ·
**6/8 walk-forward folds profitable** · Monte Carlo (2000 bootstraps) P5 +4,449, median
+12,940, **only 0.9% of resamples lose money**.

**⚠️ THE SESSION ANCHOR FLIPS THE SIGN OF THE ENTIRE RESULT.** Same data, same params:

| anchor_hour | return | expectancy |
|---|---|---|
| 0 (UTC midnight) | **−35.5%** | −2.89 |
| 13 (US cash open) | **+129.5%** | +10.04 |
| 14 | +89.7% | +6.89 |
| 21 / 22 / 23 (CFD session) | −23% to −35% | negative |

The case that 13 is legitimate and not a cherry-pick: 13:30 UTC **is** the US equity cash
open, and session-anchored VWAP from the cash open is what "VWAP" means to an actual
Nasdaq trader — it is the a priori standard choice, not the best of six. The case for
caution: six anchors were tested and the best was reported. **Both statements are true.**
Anyone continuing this must resolve it properly rather than quoting the +129%:
- **DST is unhandled.** The US cash open is 13:30 UTC in summer and 14:30 in winter, and
  `anchor_hour` is a fixed integer. A DST-aware anchor is the correct fix and is NOT done.
- Anchoring should be fixed a priori and then left alone, never swept.

**Two more things that decide this in practice:**
- **Costs kill it between 3 and 5 bps.** +197% at 0bp, +129% at 1bp, +77% at 2bp, +37% at
  3bp, **−18% at 5bp**. NAS100 CFD spread is roughly 1-2bp, so it survives a realistic
  spread and dies on a bad one. "Edge smaller than costs" remains the live risk.
- **It is regime-dependent, in the direction mean reversion always is.** Fold returns:
  2006-2010 (crash, high vol) +36% and +26%; 2014-2018 (calm bull) −5.5% and +2.6%;
  2018-2020 (COVID) +12.7%. It earns in volatile markets and bleeds in calm trending ones.

**True out-of-sample on Stefan's own Jan 2025 export — different vendor, 5 years later,
nothing refit — is NEGATIVE at anchor 13: −2.27% over 7 trades.** Seven trades proves
nothing in either direction, and it must not be waved away OR treated as a refutation. It
is the single most important thing to extend: more 2025 months from Dukascopy.

**Still no stop-loss** (Stefan asked for none). Over 15 years that means some trades ride
far against before reverting; the Monte Carlo above says nothing about the depth of those
holds. A safety stop is the obvious next experiment.

## Update: MT5 port added (2026-08-13)

Stefan asked to backtest through MetaTrader 5. No free, open-source MQL5 port of
jdehorty's indicator exists anywhere (checked the MQL5 marketplace — only paid or
closed-source `.ex5` products, or an unfilled freelance job posting). So it was ported
from scratch: **`mt5/LorentzianClassification.mq5`**, an Expert Advisor (not a plain
indicator) since the ask was backtesting, and MT5's Strategy Tester needs an EA to place
trades and produce a performance report.

Scope matches the Python port in `strategies.py` (`LorentzianClassification`), not the
full `pine/lorentzian_full.pine` — core ML entry/exit logic only (KNN over Lorentzian
distance, regime + volatility filters, kernel trend filter, strict 4-bar exits), no
SL/TP brackets, no SMC layer. Feature/filter defaults mirror the Pine ones this file
documents above.

**One deliberate, documented deviation:** Pine's `maxBarsBack` window is anchored to the
whole loaded chart's known length (`last_bar_index`), which doesn't exist in an EA
processing bars one at a time like a live feed. The port uses a sliding "most recent N
bars" training window instead. Comment this clearly in the `.mq5` file's header — don't
let a future session "fix" it back to a Pine-literal translation without understanding
why it changed.

Built-in MT5 indicators (`iRSI`, `iCCI`, `iADX`, `iATR`, `iMA`) are used wherever they
exist instead of hand-rolling — only WaveTrend (not built into MT5) and the KNN/regime
filter/kernel regression logic are hand-rolled, using persistent recursive state
(`static`/global variables updated once per bar) rather than recomputing from scratch,
matching how Pine's own `ta.*` functions and `var`-declared state work internally. This
also matters for performance: recomputing from scratch each bar would be O(n²) even
worse than the algorithm's inherent O(n·maxBarsBack) cost.

**Status: written, NOT yet compiled.** No MQL5 compiler is available outside MetaEditor
itself. Ask Stefan to paste it into MetaEditor, compile, and report the exact error text
— same paste → error → fix loop already used for the Pine file. Don't assume it compiles
clean on the first try; two rounds were needed for the Pine file too.

## THREE different scripts exist. Do not confuse them.

A previous session missed this and it wasted the user's time. Read this table first.

| # | Name | Where it is | What it is |
|---|---|---|---|
| **1** | **The PAID one** — "Bman Studios" | **Nowhere in this repo. We never had its code.** Its settings panel is transcribed in `fixtures/paid_version_settings.md` | Closed-source, invite-only, $340/yr. Stefan trialled it. Known *only* from screenshots he took. This is the thing being investigated. |
| **2** | **The ORIGINAL** — jdehorty's | `lorentzian_classification.pine` | Free, public, MPL-2.0. "Machine Learning: Lorentzian Classification v2.0". **This is the engine inside #1.** Keep byte-identical. |
| **3** | **OURS** — the rebuild | `pine/lorentzian_full.pine` (generated) | #2 **plus** an add-on reproducing #1's extra features. This is the deliverable Stefan pastes into TradingView. Built by `build_pine.py` from #2 + `pine/addon_smc_brackets.pine`. |

In one sentence: **the seller took #2, bolted on a trade-management layer, and sells it as
#1 for $340/yr — so we rebuilt that layer ourselves on top of #2 and got #3, for free.**

The screenshots of #1 exist only in the chat that captured them. **`fixtures/paid_version_settings.md`
is the only durable record** — read it before reasoning about what the paid version does.

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

4. **"The Python port's training label is exact, confirmed against upstream."** False —
   it was sign-inverted, so the port traded the mirror image of the indicator. Fixed;
   see the ⚠️ note in the merge section above. **The lesson: the "no lookahead"
   conclusion was reached correctly and independently by two sessions, and the sign was
   still wrong.** Agreeing on *when* the label is computed says nothing about *which
   way* it points. Check both.

5. **"kNN costs ~1.4s per 40k bars."** Wrong by ~4x; that benchmark strided the distance
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
- `fixtures/paid_version_settings.md` — **the paid version's full settings panel,
  transcribed from Stefan's screenshots.** The images are not in the repo, so this is the
  only durable record of what script #1 actually contains. Evidence, not config.
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
