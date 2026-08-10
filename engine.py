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
from typing import List, Optional


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
    tr = [float("nan")]
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

    def summary(self) -> str:
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
                    pnl = (px - entry_px) * position if False else None
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
