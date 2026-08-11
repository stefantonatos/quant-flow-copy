# Trial capture checklist

**Time-critical — everything here becomes impossible when the trial lapses.**
Nothing else in this project is time-boxed. Budget ~3 hours.

Chart: `MNQ1!`, 1-minute, indicator at stock settings.

---

## 1. Signal log → `fixtures/bman_day.csv` · *most important*

One full trading day. For every arrow on the chart, one row:

```csv
ts_utc,side,entry,tp1,tp2,sl
2026-08-11T00:15:00Z,LONG,29666.25,29683.50,29700.75,29649.00
```

20–40 rows is plenty. **This is the only oracle for "did I clone it correctly."**
Without it that question is permanently unanswerable — the clone can be checked
against nothing.

Tap each arrow to read the four drawn prices. If the labels overlap, hide
Breaker Blocks temporarily to uncover them.

## 2. Stats table with the left edge of history in frame

Scroll to the oldest bar the chart will load, then screenshot so the **stats table
and the leftmost bar's date are visible together**.

On a free TradingView plan the chart holds roughly 5k–20k 1-minute bars — which
means those 197 trades likely cover **4–14 days, in-sample, on the most recent
data**. Confirming that reframes the entire claim, and costs one screenshot.

Record: leftmost date/time, rightmost date/time, Trades, Wins, Winrate.

## 3. Ablations — one toggle at a time

Reset to defaults between each. Record **signal count** and the **full stats
table** after every change.

| # | Change | What it tells us |
|---|---|---|
| 1 | Show BPR **off** | Does BPR gate entries, or is it decoration? |
| 2 | Trade With Kernel **off** | How much of the signal is the kernel filter? |
| 3 | Use Regime Filter **off** | Filter contribution |
| 4 | Use Volatility Filter **off** | Filter contribution |
| 5 | Prediction Lookahead 4 → **1** | ⚠️ see below |
| 6 | Prediction Lookahead 4 → **8** | ⚠️ see below |
| 7 | Max Bars Back 300 → **2000** | Is 300 tuned, or arbitrary? |
| 8 | Pivot Lookback 5 → **15** | Do breaker blocks affect entries at all? |

**⚠️ Rows 5 and 6 are the ones that matter.** If the stats table improves
monotonically as Prediction Lookahead increases — 1 worse than 4, 4 worse than 8 —
that is a **lookahead fingerprint**. The training label is
`sign(close[i+N] − close[i])`, so a larger `N` peeks further into the future. A
model that scores better the further ahead it peeks is scoring on information it
would not have had live. Capturing this before the trial ends means we know the
answer regardless of what the Python work turns up later.

## 4. Data Window · desktop

Hover the indicator with the Data Window panel open. It lists **every plotted
series by title with its current value** — the closest thing to a structural view
of the script that's legitimately obtainable. Screenshot it.

## 5. Alert conditions · desktop

Right-click → Add alert → open the **Condition** dropdown. It enumerates every
`alert()` / `alertcondition()` in the script by name. Internal signal names leak
the logic. Screenshot the full list.

## 6. Golden OHLC slice → `fixtures/golden_500.csv`

500 consecutive 1-minute bars, plain OHLCV, from the same window as the signal
log:

```csv
t,o,h,l,c,v
```

Needed for the Python↔Pine parity harness. If chart export isn't available on a
free plan, the NinjaTrader export covers this — just make sure it's the **same
timestamps** as the signal log so the two can be cross-referenced.

---

## Not worth capturing

- Style/Visibility tab screenshots — already have them, and they're cosmetic.
- Every settings permutation. Eight one-at-a-time ablations beat fifty
  combinations you can't attribute.
