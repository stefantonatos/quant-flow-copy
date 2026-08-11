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


SECONDS_PER_YEAR = 365.25 * 24 * 3600


def bars_per_year(bars: List["Bar"], default: float = 252.0) -> float:
    """Infer how many bars make up a year, from the actual elapsed wall time.

    Counting elapsed time rather than median bar spacing means weekends,
    holidays and session gaps are handled for free: a year of daily equity
    bars yields ~252, a year of 1-minute futures bars yields ~350k.

    Annualizing 1-minute results with the old hardcoded 252 overstated
    Sharpe by roughly sqrt(1380) ~ 37x.
    """
    if len(bars) < 3:
        return default
    span = bars[-1].t - bars[0].t
    if not (span > 0) or span != span:      # non-positive or NaN
        return default
    return len(bars) / (span / SECONDS_PER_YEAR)


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
    side: str          # "LONG", "SHORT", or "LIQUIDATED"
    entry_i: int
    entry_px: float
    exit_i: int
    exit_px: float
    pnl: float         # NET of fees - this is what win_rate/profit_factor use
    pnl_pct: float
    pnl_gross: float = 0.0   # before fees
    fees: float = 0.0        # entry fee + exit fee

    @property
    def pnl_net(self) -> float:
        """Alias for `pnl`, for callers that want the distinction spelled out."""
        return self.pnl


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

    @property
    def fees_total(self) -> float:
        return sum(t.fees for t in self.trades)

    @property
    def avg_win(self) -> float:
        w = [t.pnl for t in self.trades if t.pnl > 0]
        return sum(w) / len(w) if w else 0.0

    @property
    def avg_loss(self) -> float:
        """Mean loss as a positive number."""
        l = [-t.pnl for t in self.trades if t.pnl <= 0]
        return sum(l) / len(l) if l else 0.0

    @property
    def payoff_ratio(self) -> float:
        return (self.avg_win / self.avg_loss) if self.avg_loss > 0 else float("inf")

    @property
    def breakeven_win_rate(self) -> float:
        """Win rate this strategy needs, at its realized payoff ratio, to break even.

        The gap between this and the actual win rate is the edge. A 72% win
        rate means nothing until you know whether breakeven sits at 50% or 71%.
        """
        r = self.payoff_ratio
        if r == float("inf"):
            return 0.0
        return 1.0 / (1.0 + r)

    @property
    def expectancy(self) -> float:
        """Mean net P&L per trade, in currency."""
        return (sum(t.pnl for t in self.trades) / len(self.trades)
                if self.trades else 0.0)

    def summary(self) -> str:
        gross = sum(t.pnl_gross for t in self.trades)
        cost_share = (self.fees_total / gross * 100) if gross > 0 else float("nan")
        lines = []
        lines.append("=" * 56)
        lines.append("BACKTEST REPORT")
        lines.append("=" * 56)
        lines.append(f"Initial capital   : {self.initial:,.2f}")
        lines.append(f"Final equity      : {self.final:,.2f}")
        lines.append(f"Total return      : {self.total_return*100:,.2f}%")
        lines.append(f"Max drawdown      : {self.max_dd*100:,.2f}%")
        lines.append(f"Sharpe (ann.)     : {self.sharpe:,.2f}")
        lines.append("-" * 56)
        lines.append(f"Trades            : {len(self.trades)}")
        lines.append(f"Expectancy/trade  : {self.expectancy:,.2f}   <-- read this first")
        lines.append(f"Win rate          : {self.win_rate*100:,.1f}%"
                     f"   (breakeven: {self.breakeven_win_rate*100:,.1f}%)")
        lines.append(f"Payoff ratio      : {self.payoff_ratio:,.2f}"
                     f"   (avg win {self.avg_win:,.2f} / avg loss {self.avg_loss:,.2f})")
        lines.append(f"Profit factor     : {self.profit_factor:,.2f}  (net of fees)")
        lines.append("-" * 56)
        lines.append(f"Gross P&L         : {gross:,.2f}")
        lines.append(f"Fees paid         : {self.fees_total:,.2f}"
                     + (f"   ({cost_share:,.1f}% of gross profit)"
                        if cost_share == cost_share else ""))
        lines.append(f"Net P&L           : {sum(t.pnl for t in self.trades):,.2f}")
        if any(t.side == "LIQUIDATED" for t in self.trades):
            lines.append("!! ACCOUNT WAS LIQUIDATED - trading halted mid-run")
        lines.append("=" * 56)
        return "\n".join(lines)


def run(strategy: Strategy, initial: float = 10000.0,
        fee_bps: float = 0.0, max_leverage: float = 1.0) -> Report:
    bars = strategy.bars
    cash = initial
    position = 0.0          # units; negative = short
    entry_px = 0.0
    entry_side: Optional[str] = None
    entry_i = 0
    entry_fee = 0.0
    equity: List[float] = []
    trades: List[Trade] = []
    liquidated = False

    fee = fee_bps / 10000.0

    def equity_now(px):
        return cash + position * px

    def close_position(i: int, px: float, reason: Optional[str] = None):
        """Flatten at `px`, booking fees INTO the trade record."""
        nonlocal cash, position, entry_side, entry_fee
        proceeds = position * px
        cash += proceeds
        exit_fee = abs(proceeds) * fee
        cash -= exit_fee
        if entry_side == "LONG":
            gross = (px - entry_px) * abs(position)
        else:
            gross = (entry_px - px) * abs(position)
        fees = entry_fee + exit_fee
        net = gross - fees
        notional = entry_px * abs(position)
        trades.append(Trade(
            side=reason or entry_side,
            entry_i=entry_i, exit_i=i,
            entry_px=entry_px, exit_px=px,
            pnl=net, pnl_pct=(net / notional) if notional else 0.0,
            pnl_gross=gross, fees=fees,
        ))
        position = 0.0
        entry_side = None
        entry_fee = 0.0

    def open_position(i: int, px: float, side: str):
        nonlocal cash, position, entry_px, entry_side, entry_i, entry_fee
        # Size off equity (== cash here, since we are flat) with a leverage cap.
        # The old code sized off `cash` alone, which after a losing short could
        # be negative -- producing a negative "LONG" position.
        eq = cash
        if eq <= 0 or px <= 0:
            return
        units = (eq * max_leverage) / px
        notional = units * px
        entry_fee = notional * fee
        cash += -notional if side == "LONG" else notional
        cash -= entry_fee
        position = units if side == "LONG" else -units
        entry_px = px
        entry_side = side
        entry_i = i

    for i in range(len(bars)):
        px = bars[i].c
        if i > 0 and not liquidated:
            target = strategy.decide(i)
            if target != entry_side:
                if position != 0:
                    close_position(i, px)
                if target in ("LONG", "SHORT"):
                    open_position(i, px, target)

        # Margin call. The old engine let a short ride to -17,600 on a 10,000
        # account and then opened a sign-inverted "LONG" on negative cash.
        #
        # Liquidation is intrabar, not at the close: a broker flattens you when
        # equity touches zero, so we exit at the zero-equity price rather than
        # letting the bar close somewhere far below it. Only bars the position
        # was actually held into can trigger it -- we enter at the close, so
        # the entry bar's own range is already history.
        if not liquidated and position != 0 and entry_i < i:
            px_liq = -cash / position
            breached = (bars[i].l <= px_liq) if position > 0 else (bars[i].h >= px_liq)
            if breached:
                close_position(i, px_liq, reason="LIQUIDATED")
                liquidated = True

        equity.append(equity_now(px))

    # Flatten any open position at the final close so reported trades and the
    # equity curve agree.
    if position != 0 and bars:
        close_position(len(bars) - 1, bars[-1].c)
        equity[-1] = equity_now(bars[-1].c)

    final = equity[-1] if equity else initial
    total_return = final / initial - 1.0

    # Drawdown
    peak = equity[0] if equity else initial
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

    # Sharpe on per-bar returns, annualized by the bar size actually present.
    # The old code hardcoded sqrt(252); on 1-minute bars that overstated
    # Sharpe by roughly sqrt(1380) ~ 37x.
    ppy = bars_per_year(bars)
    rets = []
    for a, b in zip(equity[1:], equity[:-1]):
        if b > 0:
            rets.append(a / b - 1.0)
    if len(rets):
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / len(rets)
        sd = math.sqrt(var) if var > 0 else 0.0
        sharpe = (m / sd * math.sqrt(ppy)) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    return Report(
        equity=equity, trades=trades, initial=initial, final=final,
        total_return=total_return, max_dd=max_dd, win_rate=win_rate,
        profit_factor=profit_factor, sharpe=sharpe, bars=len(bars),
    )
