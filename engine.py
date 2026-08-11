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


def wavetrend(vals: List[float], n1: int = 10, n2: int = 11):
    """Wave Trend oscillator (wt1, wt2), as used by the LazyBear/jdehorty
    feature set for ML classification."""
    e1 = ema(vals, n1)
    diff = [abs(v - e) if e == e else float("nan") for v, e in zip(vals, e1)]
    diff_clean = [x if x == x else 0.0 for x in diff]
    e2 = ema(diff_clean, n1)
    e2 = [x if diff[i] == diff[i] else float("nan") for i, x in enumerate(e2)]
    ci = [float("nan")] * len(vals)
    for i, (v, a, b) in enumerate(zip(vals, e1, e2)):
        if a == a and b == b and b != 0:
            ci[i] = (v - a) / (0.015 * b)
    ci_clean = [x if x == x else 0.0 for x in ci]
    wt1 = ema(ci_clean, n2)
    wt1 = [x if ci[i] == ci[i] else float("nan") for i, x in enumerate(wt1)]
    wt1_clean = [x if x == x else 0.0 for x in wt1]
    wt2 = sma(wt1_clean, 4)
    wt2 = [x if wt1[i] == wt1[i] else float("nan") for i, x in enumerate(wt2)]
    return wt1, wt2


def cci(bars: List[Bar], n: int = 20) -> List[float]:
    tp = [(b.h + b.l + b.c) / 3.0 for b in bars]
    tp_sma = sma(tp, n)
    out = [float("nan")] * len(bars)
    for i in range(len(bars)):
        if tp_sma[i] != tp_sma[i]:
            continue
        window = tp[i - n + 1 : i + 1]
        mean = tp_sma[i]
        mad = sum(abs(x - mean) for x in window) / n
        out[i] = (tp[i] - mean) / (0.015 * mad) if mad > 0 else 0.0
    return out


def _wilder_sum(vals: List[float], n: int) -> List[float]:
    """Wilder's running-sum smoothing (used for DMI, not a plain average)."""
    out = [0.0] * len(vals)
    if len(vals) < n:
        return out
    s = sum(vals[:n])
    out[n - 1] = s
    for i in range(n, len(vals)):
        s = s - s / n + vals[i]
        out[i] = s
    return out


def adx(bars: List[Bar], n: int = 14) -> List[float]:
    """Average Directional Index, Wilder's method."""
    ln = len(bars)
    plus_dm = [0.0] * ln
    minus_dm = [0.0] * ln
    tr = [0.0] * ln
    for i in range(1, ln):
        up = bars[i].h - bars[i - 1].h
        dn = bars[i - 1].l - bars[i].l
        plus_dm[i] = up if (up > dn and up > 0) else 0.0
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(bars[i].h - bars[i].l, abs(bars[i].h - bars[i - 1].c), abs(bars[i].l - bars[i - 1].c))
    tr_s = _wilder_sum(tr, n)
    pdm_s = _wilder_sum(plus_dm, n)
    mdm_s = _wilder_sum(minus_dm, n)
    pdi = [100 * p / t if t else 0.0 for p, t in zip(pdm_s, tr_s)]
    mdi = [100 * m / t if t else 0.0 for m, t in zip(mdm_s, tr_s)]
    dx = [100 * abs(p - m) / (p + m) if (p + m) else 0.0 for p, m in zip(pdi, mdi)]
    out = _wilder_sum(dx, n)
    out = [x / n if x else 0.0 for x in out]  # Wilder's DX smoothing is an average, unlike TR/DM
    for i in range(min(2 * n, ln)):
        out[i] = float("nan")
    return out


def normalize01(vals: List[float]) -> List[float]:
    """Running historic min-max normalize to [0, 1] (matches jdehorty's
    MLExtensions `normalize`, used for unbounded features like WT/CCI)."""
    out = [float("nan")] * len(vals)
    lo, hi = float("inf"), float("-inf")
    for i, v in enumerate(vals):
        if v != v:
            continue
        lo, hi = min(lo, v), max(hi, v)
        span = hi - lo
        out[i] = (v - lo) / span if span > 1e-10 else 0.5
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
        lines.append(f"Verdict           : {grade}"
                     + (f"  ({', '.join(notes)})" if notes else ""))
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
