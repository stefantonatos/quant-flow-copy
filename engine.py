"""
engine.py - Dependency-free backtesting engine (stdlib only).

A small event-driven, long/flat/short backtester. Strategies subclass
`Strategy` and implement `decide(i)` which returns a target posture for
bar `i`: "LONG", "FLAT", or "SHORT". The engine executes transitions at
the close of bar `i` and tracks an equity curve.

This mirrors the "Validate & Save / backtest anything" loop sold by
LuxAlgo Quant and QuantPad, but with no proprietary model and no paid feed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Bar:
    t: float          # epoch seconds
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0

    @property
    def mid(self) -> float:
        return (self.h + self.l) / 2.0


# ----------------------------------------------------------------------------
# Indicator helpers (pure python, NaN-aware)
# ----------------------------------------------------------------------------
def sma(vals: List[float], n: int) -> List[float]:
    if n <= 0:
        raise ValueError("n must be > 0")
    out = [float("nan")] * len(vals)
    if len(vals) < n:
        return out
    acc = sum(vals[:n])
    out[n - 1] = acc / n
    for i in range(n, len(vals)):
        acc += vals[i] - vals[i - n]
        out[i] = acc / n
    return out


def ema(vals: List[float], n: int) -> List[float]:
    if n <= 0 or not vals:
        return [float("nan")] * len(vals)
    out = [float("nan")] * len(vals)
    k = 2.0 / (n + 1)
    prev = vals[0]
    for i, x in enumerate(vals):
        prev = x if i == 0 else x * k + prev * (1 - k)
        out[i] = prev if i >= n - 1 else float("nan")
    return out


def rsi(vals: List[float], n: int = 14) -> List[float]:
    out = [float("nan")] * len(vals)
    if len(vals) < n + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, len(vals)):
        ch = vals[i] - vals[i - 1]
        g = max(ch, 0.0)
        l = max(-ch, 0.0)
        if i <= n:
            gains += g
            losses += l
            if i == n:
                rs = (gains / n) / (losses / n) if losses > 0 else float("inf")
                out[i] = 100.0 if losses == 0 else 100 - 100 / (1 + rs)
        else:
            gains = (gains * (n - 1) + g) / n
            losses = (losses * (n - 1) + l) / n
            rs = gains / losses if losses > 0 else float("inf")
            out[i] = 100.0 if losses == 0 else 100 - 100 / (1 + rs)
    return out


def atr(bars: List[Bar], n: int = 14) -> List[float]:
    # tr[0] has no prior close, so use the day's own range (standard
    # convention) rather than NaN -- sma()'s rolling accumulator would
    # otherwise be permanently poisoned by a single leading NaN.
    tr = [bars[0].h - bars[0].l] if bars else []
    for i in range(1, len(bars)):
        hp = bars[i].h - bars[i].l
        hc = abs(bars[i].h - bars[i - 1].c)
        lc = abs(bars[i].l - bars[i - 1].c)
        tr.append(max(hp, hc, lc))
    return sma(tr, n)


def bollinger(vals: List[float], n: int = 20, k: float = 2.0):
    mid = sma(vals, n)
    upper, lower = [float("nan")] * len(vals), [float("nan")] * len(vals)
    for i in range(len(vals)):
        if math.isnan(mid[i]):
            continue
        mean = mid[i]
        var = sum((vals[j] - mean) ** 2 for j in range(i - n + 1, i + 1)) / n
        sd = math.sqrt(var)
        upper[i] = mean + k * sd
        lower[i] = mean - k * sd
    return lower, mid, upper


def true_range_series(bars: List[Bar]) -> List[float]:
    tr = [float("nan")]
    for i in range(1, len(bars)):
        hp = bars[i].h - bars[i].l
        hc = abs(bars[i].h - bars[i - 1].c)
        lc = abs(bars[i].l - bars[i - 1].c)
        tr.append(max(hp, hc, lc))
    return tr


def highest(vals: List[float], n: int) -> List[float]:
    out = [float("nan")] * len(vals)
    for i in range(len(vals)):
        if i >= n - 1:
            out[i] = max(vals[i - n + 1 : i + 1])
    return out


def lowest(vals: List[float], n: int) -> List[float]:
    out = [float("nan")] * len(vals)
    for i in range(len(vals)):
        if i >= n - 1:
            out[i] = min(vals[i - n + 1 : i + 1])
    return out


def macd(vals: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram)."""
    fast_e = ema(vals, fast)
    slow_e = ema(vals, slow)
    line = [a - b if a == a and b == b else float("nan") for a, b in zip(fast_e, slow_e)]
    clean = [x if x == x else 0.0 for x in line]
    sig = ema(clean, signal)
    sig = [s if line[i] == line[i] else float("nan") for i, s in enumerate(sig)]
    hist = [l - s if l == l and s == s else float("nan") for l, s in zip(line, sig)]
    return line, sig, hist


def stochastic(bars: List[Bar], n: int = 14, d: int = 3):
    """Returns (%K, %D)."""
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    closes = [b.c for b in bars]
    hh = highest(highs, n)
    ll = lowest(lows, n)
    k = [float("nan")] * len(bars)
    for i in range(len(bars)):
        if hh[i] == hh[i] and ll[i] == ll[i] and hh[i] != ll[i]:
            k[i] = 100.0 * (closes[i] - ll[i]) / (hh[i] - ll[i])
    kd = [x if x == x else 0.0 for x in k]
    dline = sma(kd, d)
    dline = [x if k[i] == k[i] else float("nan") for i, x in enumerate(dline)]
    return k, dline


def vwap_rolling(bars: List[Bar], n: int = 20) -> List[float]:
    """Rolling (non-anchored) VWAP over the last n bars — an approximation
    since these adapters return daily bars with no intraday session to
    anchor a true session VWAP to."""
    tp = [(b.h + b.l + b.c) / 3.0 for b in bars]
    vol = [b.v for b in bars]
    out = [float("nan")] * len(bars)
    for i in range(len(bars)):
        if i >= n - 1:
            num = sum(tp[j] * vol[j] for j in range(i - n + 1, i + 1))
            den = sum(vol[j] for j in range(i - n + 1, i + 1))
            out[i] = num / den if den > 0 else tp[i]
    return out


def supertrend(bars: List[Bar], n: int = 10, mult: float = 3.0):
    """Returns (line, direction) where direction[i] is 1 (up/long bias) or
    -1 (down/short bias)."""
    a = atr(bars, n)
    line = [float("nan")] * len(bars)
    direction = [0] * len(bars)
    up_band = float("nan")
    dn_band = float("nan")
    trend = 1
    for i in range(len(bars)):
        if a[i] != a[i]:
            continue
        mid = (bars[i].h + bars[i].l) / 2.0
        basic_up = mid + mult * a[i]
        basic_dn = mid - mult * a[i]
        if up_band != up_band:
            up_band, dn_band = basic_up, basic_dn
        else:
            prev_close = bars[i - 1].c
            up_band = basic_up if (basic_up < up_band or prev_close > up_band) else up_band
            dn_band = basic_dn if (basic_dn > dn_band or prev_close < dn_band) else dn_band
        c = bars[i].c
        if trend == 1 and c < dn_band:
            trend = -1
        elif trend == -1 and c > up_band:
            trend = 1
        direction[i] = trend
        line[i] = dn_band if trend == 1 else up_band
    return line, direction


def roc(vals: List[float], n: int = 10) -> List[float]:
    """Rate of change, percent."""
    out = [float("nan")] * len(vals)
    for i in range(n, len(vals)):
        prev = vals[i - n]
        if prev:
            out[i] = (vals[i] - prev) / prev * 100.0
    return out


# ----------------------------------------------------------------------------
# Strategy base + trade record
# ----------------------------------------------------------------------------
@dataclass
class Trade:
    side: str          # "LONG" or "SHORT"
    entry_i: int
    entry_px: float
    exit_i: int
    exit_px: float
    pnl: float
    pnl_pct: float


class Strategy:
    """Subclass and implement `decide(i) -> 'LONG'|'FLAT'|'SHORT'`."""

    name = "base"
    description = ""

    def __init__(self, bars: Optional[List[Bar]] = None, params: Optional[dict] = None):
        self.bars = bars or []
        self.closes = [b.c for b in self.bars]
        self.params = params or {}
        if self.bars:
            self.prepare()

    def prepare(self):
        """Precompute any indicators here."""
        pass

    def decide(self, i: int) -> str:
        return "FLAT"

    def to_pine(self) -> str:
        return "// Pine Script equivalent not defined for this strategy."


# ----------------------------------------------------------------------------
# Backtest runner
# ----------------------------------------------------------------------------
@dataclass
class Report:
    equity: List[float]
    trades: List[Trade]
    initial: float
    final: float
    total_return: float
    max_dd: float
    win_rate: float
    profit_factor: float
    sharpe: float
    bars: int

    def verdict(self) -> Tuple[str, List[str]]:
        """Heuristic A-F grade from edge/robustness/risk/sample-size, in the
        spirit of a "verdict score" — not a statistical guarantee."""
        score = 0
        notes = []
        if self.sharpe > 1:
            score += 2
        elif self.sharpe > 0:
            score += 1
        else:
            score -= 1
        if self.profit_factor > 1.5:
            score += 2
        elif self.profit_factor > 1:
            score += 1
        else:
            score -= 1
        if self.max_dd < 0.15:
            score += 1
        elif self.max_dd > 0.4:
            score -= 1
            notes.append("deep drawdown")
        n = len(self.trades)
        if n >= 20:
            score += 1
        elif n < 10:
            score -= 1
            notes.append(f"low sample size ({n} trades)")
        if score >= 5:
            grade = "A"
        elif score >= 3:
            grade = "B"
        elif score >= 1:
            grade = "C"
        elif score >= -1:
            grade = "D"
        else:
            grade = "F"
        return grade, notes

    def summary(self) -> str:
        grade, notes = self.verdict()
        lines = []
        lines.append("=" * 56)
        lines.append("BACKTEST REPORT")
        lines.append("=" * 56)
        lines.append(f"Initial capital   : {self.initial:,.2f}")
        lines.append(f"Final equity      : {self.final:,.2f}")
        lines.append(f"Total return      : {self.total_return*100:,.2f}%")
        lines.append(f"Max drawdown      : {self.max_dd*100:,.2f}%")
        lines.append(f"Trades            : {len(self.trades)}")
        lines.append(f"Win rate          : {self.win_rate*100:,.1f}%")
        lines.append(f"Profit factor     : {self.profit_factor:,.2f}")
        lines.append(f"Sharpe (ann.)     : {self.sharpe:,.2f}")
        lines.append(f"Verdict           : {grade}" + (f"  ({', '.join(notes)})" if notes else ""))
        lines.append("=" * 56)
        return "\n".join(lines)


def run(strategy: Strategy, initial: float = 10000.0,
        fee_bps: float = 0.0) -> Report:
    bars = strategy.bars
    closes = [b.c for b in bars]
    cash = initial
    position = 0.0          # units; negative = short
    entry_px = 0.0
    entry_side = None
    equity = []
    trades: List[Trade] = []

    fee = fee_bps / 10000.0

    def equity_now(px):
        return cash + position * px

    for i in range(len(bars)):
        px = bars[i].c
        if i > 0:
            target = strategy.decide(i)
            # Execute transition at close of bar i
            if target != entry_side:
                # Close existing
                if position != 0:
                    proceeds = position * px
                    cash += proceeds
                    # fee on notional
                    cash -= abs(proceeds) * fee
                    if entry_side == "LONG":
                        pnl = (px - entry_px) * abs(position)
                    else:
                        pnl = (entry_px - px) * abs(position)
                    trades.append(Trade(
                        side=entry_side,
                        entry_i=0, exit_i=i,  # entry_i tracked below
                        entry_px=entry_px, exit_px=px,
                        pnl=pnl, pnl_pct=pnl / (entry_px * abs(position)) if entry_px else 0,
                    ))
                    # fix entry_i using stored value
                    trades[-1].entry_i = _entry_idx
                    position = 0.0
                    entry_side = None
                # Open new
                if target == "LONG":
                    units = cash / px
                    cash -= units * px
                    cash -= abs(units * px) * fee
                    position = units
                    entry_px = px
                    entry_side = "LONG"
                    _entry_idx = i
                elif target == "SHORT":
                    units = cash / px
                    cash += units * px
                    cash -= abs(units * px) * fee
                    position = -units
                    entry_px = px
                    entry_side = "SHORT"
                    _entry_idx = i
        eq = equity_now(px)
        equity.append(eq)

    # Final liquidation at last close for reporting
    final = equity[-1]
    total_return = final / initial - 1.0

    # Drawdown
    peak = equity[0]
    max_dd = 0.0
    for eq in equity:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    # Sharpe on per-bar returns (annualized assuming ~252 bars/yr proxy)
    rets = []
    for a, b in zip(equity[1:], equity[:-1]):
        if b > 0:
            rets.append(a / b - 1.0)
    if len(rets):
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / len(rets)
        sd = math.sqrt(var) if var > 0 else 0.0
        sharpe = (m / sd * math.sqrt(252)) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    return Report(
        equity=equity, trades=trades, initial=initial, final=final,
        total_return=total_return, max_dd=max_dd, win_rate=win_rate,
        profit_factor=profit_factor, sharpe=sharpe, bars=len(bars),
    )
