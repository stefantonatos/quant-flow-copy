# The paid indicator's settings panel — transcribed from screenshots

The paid script ("Bman Studios", invite-only, $340/yr) is **closed source**. We never had
its code. Everything known about it comes from screenshots of its settings panel taken
during a 7-day trial, transcribed here because the images themselves live only in the
chat that captured them.

Chart context: **MNQ1! (Micro E-mini Nasdaq-100), 1-minute.** Captured 2026-08-11.

This file is evidence, not configuration. Do not edit it to match anything.

---

## Inputs tab

### General Settings
| Setting | Value |
|---|---|
| Neighbors Count (K) | 8 |
| Max Bars Back (training samples) | **300** |
| Prediction Lookahead (bars) | **4** |
| Feature Normalization Length | **100** |

### Feature Engineering
| Setting | Value |
|---|---|
| Feature Count | 5 |
| Feature 1 Type / Param A / Param B | RSI / 14 / 1 |
| Feature 2 Type / Param A / Param B | WT / 10 / 11 |
| Feature 3 Type / Param A / Param B | CCI / 20 / 1 |
| Feature 4 Type / Param A / Param B | ADX / 20 / 2 |
| Feature 5 Type / Param A / Param B | RSI / 9 / 1 |

### Filters
| Setting | Value |
|---|---|
| Use Volatility Filter | ✅ on |
| Use Regime Filter | ✅ on |
| Regime Threshold | −0.1 |
| Use ADX Filter | ☐ off |
| ADX Threshold | 20 |
| Use EMA Filter | ☐ off |
| EMA Filter Period | 200 |
| Use SMA Filter | ☐ off |
| SMA Filter Period | 200 |

### Kernel Settings
| Setting | Value |
|---|---|
| Trade With Kernel | ✅ on |
| Show Kernel Estimate | ✅ on |
| Lookback Window | 8 |
| Relative Weighting | 8 |
| Enhance Kernel Smoothing | ☐ off |
| Smoothing Lag | 2 |

**No "Regression Level" input exists** (upstream has one, `x`, default 25).

### Breaker Blocks
| Setting | Value |
|---|---|
| Pivot Lookback (Swing Detection) | 5 |
| Max Tracked Order Blocks | 25 |

### Balanced Price Range
| Setting | Value |
|---|---|
| Show BPR (overlapping FVGs) | ✅ on |

### Take Profit / Stop Loss (PD Arrays)
| Setting | Value |
|---|---|
| SL Buffer (x ATR) | 0.25 |
| Extra SL Distance (x ATR) | 0.75 |
| Trade Zone Width (bars) | 30 |
| Show TP/SL Levels | ✅ on |

### Session-Based Tightening
| Setting | Value |
|---|---|
| Tighten SL/TP during Asia & London | ✅ on |
| Asia Session (exchange time) | 00:00 – 09:00 |
| London Session (exchange time) | 07:00 – 16:00 |
| Tighten Amount (x ATR) | 0.25 |
| Minimum SL Distance (x ATR) | 0.1 |

### Trade Stats
| Setting | Value |
|---|---|
| Show Trade Stats | ✅ on |
| Breakeven+ Offset (x ATR) | 0.15 |

### Display
Bullish Color (teal-green) · Bearish Color (red). Nothing else.

## Style tab

Kernel Regression Estimate ✅ · Buy Signal ✅ (triangle up, below bar) · Sell Signal ✅
(triangle down, above bar) · Boxes ✅ · Pane labels ✅ · Lines ✅ · Tables ✅ ·
Precision Default · Labels on price scale ✅ · Values in status line ✅ · Inputs in
status line ✅.

## On-chart evidence

Status line read: `B... 8 300 4 100 5 RSI 14 1 WT 10 11 CCI 20` — confirming the input
order matches upstream's, with 300 / 4 / 100 inserted.

Its stats table:

| | |
|---|---|
| Trades | 197 |
| Wins | 142 |
| Winrate | 72.1% |
| W/L Ratio | 2.58 |

A single trade's drawn levels:

| Level | Price | Distance from entry |
|---|---|---|
| TP2 | 29700.75 | +34.50 (**exactly 2.00R**) |
| TP1 | 29683.50 | +17.25 (**exactly 1.00R**) |
| Entry | 29666.25 | — |
| SL | 29649.00 | −17.25 (1R) |

---

## What this transcription establishes

**Every General/Feature/Filter/Kernel default matches jdehorty's public MPL-2.0 script
exactly, and in the same order.** Nobody independently arrives at
`WT 10,11` → `CCI 20,1` → `ADX 20,2`. That is the basis for the whole finding.

**Present here, absent upstream** — the seller's genuine additions: Breaker Blocks,
Balanced Price Range, Take Profit / Stop Loss (PD Arrays), Session-Based Tightening,
Breakeven+ Offset.

**Present upstream, deleted here** — `Use Worst Case Estimates`, `Regression Level`,
`Color Compression`, `Show Default Exits`, `Use Dynamic Exits`, `Include Full History`,
and the bar-coloring options.

The `Use Worst Case Estimates` deletion is the one that matters: upstream defaults it to
**off**, so the 72.1% is scored on optimistic mid-bar entries without avoiding intrabar
repainting — and with the switch removed, the conservative number cannot be reached in
the paid version.
