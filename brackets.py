"""
brackets.py - Bracket-order backtester: TP1/TP2/SL execution against
high/low, not just closes.

engine.run() is close/flat/short only -- no stop, target, or intrabar concept
exists there. That's fine for the strategies it was built for, but it can't
reproduce what an on-chart vendor stats table actually measures: a fixed-R
bracket opened on a signal, with a stop and two targets that can be touched
mid-bar. This module is that missing piece, built to mirror the geometry
observed on the paid indicator's chart and reproduced in
pine/addon_smc_brackets.pine -- SL at 1.0x ATR, TP1 at 1R, TP2 at 2R, half
size off at TP1 with the stop walked to breakeven+.

The central problem intrabar brackets always have: a 1-minute bar's high can
touch a target and its low can touch the stop in the same bar, and OHLC data
alone can't say which happened first. Two policies are provided, and the
honest move is to always report both:

  pessimistic (default) - the stop wins any same-bar ambiguity
  optimistic             - the target wins any same-bar ambiguity

A backtest that only reports the optimistic number is exactly the trick that
manufactures an inflated win rate. See CLAUDE.md's notes on the vendor's
72.1% claim for why this distinction is the whole point of this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional

from engine import Bar, atr

Policy = Literal["pessimistic", "optimistic"]


@dataclass
class BracketFill:
    i: int
    px: float
    reason: str   # "tp1" | "tp2" | "stop" | "eod"


@dataclass
class BracketTrade:
    side: str            # "LONG" or "SHORT"
    entry_i: int
    entry_px: float
    stop0: float          # initial stop; |entry_px - stop0| defines 1R
    tp1: float
    tp2: float
    risk: float
    fills: List[BracketFill] = field(default_factory=list)
    exit_i: int = -1
    exit_reason: str = ""
    r_multiple: float = 0.0
    ambiguous: bool = False   # a same-bar stop+target tie was resolved by policy


@dataclass
class BracketReport:
    trades: List[BracketTrade]
    policy: Policy

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.r_multiple > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.r_multiple <= 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def avg_r(self) -> float:
        """Expectancy in R -- the number that actually matters."""
        return sum(t.r_multiple for t in self.trades) / self.n if self.n else 0.0

    @property
    def total_r(self) -> float:
        return sum(t.r_multiple for t in self.trades)

    @property
    def ambiguous_count(self) -> int:
        return sum(1 for t in self.trades if t.ambiguous)

    @property
    def avg_win_r(self) -> float:
        w = [t.r_multiple for t in self.trades if t.r_multiple > 0]
        return sum(w) / len(w) if w else 0.0

    @property
    def avg_loss_r(self) -> float:
        """Mean loss as a positive number, in R."""
        l = [-t.r_multiple for t in self.trades if t.r_multiple <= 0]
        return sum(l) / len(l) if l else 0.0

    @property
    def payoff_ratio(self) -> float:
        return (self.avg_win_r / self.avg_loss_r) if self.avg_loss_r > 0 else float("inf")

    @property
    def breakeven_win_rate(self) -> float:
        """Derived from the REALIZED payoff, never assumed.

        Hardcoding 50% only holds for a 1:1 bracket. This strategy family
        runs 2.5R targets, where breakeven is nearer 29% -- quoting 50%
        there would make a perfectly good system look like a losing one.
        """
        r = self.payoff_ratio
        return 0.0 if r == float("inf") else 1.0 / (1.0 + r)

    def summary(self) -> str:
        lines = []
        lines.append("=" * 56)
        lines.append(f"BRACKET REPORT ({self.policy})")
        lines.append("=" * 56)
        lines.append(f"Trades            : {self.n}")
        lines.append(f"Expectancy        : {self.avg_r:+.3f} R   <-- read this first")
        lines.append(f"Win rate          : {self.win_rate*100:.1f}%"
                     f"   (breakeven: {self.breakeven_win_rate*100:.1f}%)")
        lines.append(f"Payoff ratio      : {self.payoff_ratio:.2f}"
                     f"   (avg win {self.avg_win_r:+.2f}R / avg loss -{self.avg_loss_r:.2f}R)")
        lines.append(f"Total R           : {self.total_r:+.2f}")
        lines.append(f"Ambiguous bars    : {self.ambiguous_count}/{self.n}"
                     f"  (same-bar stop+target ties, resolved {self.policy})")
        lines.append("=" * 56)
        return "\n".join(lines)


def _build_bracket(bars: List[Bar], i: int, side: str, atr_val: float,
                    sl_atr: float, be_offset_atr: float,
                    stop_px: Optional[float] = None,
                    target_px: Optional[float] = None) -> BracketTrade:
    """`stop_px` / `target_px` let a strategy supply its own structural levels
    (e.g. a stop below a demand zone, a target at the last swing high) instead
    of having them derived from ATR. When neither is given the behaviour is
    exactly the original ATR bracket -- SL at sl_atr x ATR, TP1 1R, TP2 2R."""
    entry_px = bars[i].c

    if stop_px is not None and stop_px == stop_px:
        risk = abs(entry_px - stop_px)
        stop0 = stop_px
    else:
        risk = sl_atr * atr_val
        stop0 = entry_px - risk if side == "LONG" else entry_px + risk

    if target_px is not None and target_px == target_px:
        # Single structural target: TP2 is the real exit, TP1 sits at the
        # halfway point so the partial-exit machinery still applies.
        tp2 = target_px
        tp1 = entry_px + (tp2 - entry_px) / 2.0
    elif side == "LONG":
        tp1, tp2 = entry_px + risk, entry_px + 2 * risk
    else:
        tp1, tp2 = entry_px - risk, entry_px - 2 * risk

    return BracketTrade(side=side, entry_i=i, entry_px=entry_px,
                        stop0=stop0, tp1=tp1, tp2=tp2, risk=risk)


def _resolve(trade: BracketTrade, bars: List[Bar], be_offset_atr: float,
             atr_val: float, policy: Policy) -> None:
    """Walk bars forward from entry_i+1, filling the trade in place."""
    long = trade.side == "LONG"
    half_off = False
    stop = trade.stop0
    be_price = (trade.entry_px + be_offset_atr * atr_val if long
                else trade.entry_px - be_offset_atr * atr_val)

    for j in range(trade.entry_i + 1, len(bars)):
        b = bars[j]

        # Gaps first: if the bar opens through a level, that level fills at
        # the open, not at the level itself -- filling at the level when
        # price gapped straight through it is free money that never existed.
        if long:
            if b.o <= stop:
                trade.fills.append(BracketFill(j, b.o, "stop"))
                trade.exit_i, trade.exit_reason = j, "stop"
                break
            if not half_off and b.o >= trade.tp2:
                trade.fills.append(BracketFill(j, b.o, "tp2"))
                trade.exit_i, trade.exit_reason = j, "tp2"
                break
            if b.o >= trade.tp1 and not half_off:
                trade.fills.append(BracketFill(j, b.o, "tp1"))
                half_off = True
                stop = be_price
                continue
        else:
            if b.o >= stop:
                trade.fills.append(BracketFill(j, b.o, "stop"))
                trade.exit_i, trade.exit_reason = j, "stop"
                break
            if not half_off and b.o <= trade.tp2:
                trade.fills.append(BracketFill(j, b.o, "tp2"))
                trade.exit_i, trade.exit_reason = j, "tp2"
                break
            if b.o <= trade.tp1 and not half_off:
                trade.fills.append(BracketFill(j, b.o, "tp1"))
                half_off = True
                stop = be_price
                continue

        # Intrabar. Never let the entry bar's own range fill anything --
        # that's classic lookahead. j already starts at entry_i + 1.
        hit_stop = (b.l <= stop) if long else (b.h >= stop)
        target = trade.tp2 if half_off else trade.tp1
        hit_target = (b.h >= target) if long else (b.l <= target)

        if hit_stop and hit_target:
            trade.ambiguous = True
            stop_wins = (policy == "pessimistic")
        elif hit_stop:
            stop_wins = True
        elif hit_target:
            stop_wins = False
        else:
            continue

        if stop_wins:
            trade.fills.append(BracketFill(j, stop, "stop"))
            trade.exit_i, trade.exit_reason = j, "stop"
            break
        elif half_off:
            trade.fills.append(BracketFill(j, trade.tp2, "tp2"))
            trade.exit_i, trade.exit_reason = j, "tp2"
            break
        else:
            trade.fills.append(BracketFill(j, trade.tp1, "tp1"))
            half_off = True
            stop = be_price
            # Breakeven is deferred to the NEXT bar -- resolving a second
            # intrabar event within the bar that just filled TP1 would
            # reintroduce exactly the ambiguity this function exists to bound.
            continue

    if trade.exit_i == -1:
        # Never closed: flatten at the final bar's close.
        j = len(bars) - 1
        trade.fills.append(BracketFill(j, bars[j].c, "eod"))
        trade.exit_i, trade.exit_reason = j, "eod"

    _score(trade, half_off)


def _score(trade: BracketTrade, half_off: bool) -> None:
    """Score every leg from its ACTUAL fill price.

    Nothing here may assume TP1 == 1R or TP2 == 2R: with a structural target
    (a stop below a demand zone, a target at the last swing high) those legs
    land wherever the chart puts them. Deriving R from real fill prices also
    handles gaps correctly for free -- a stop that gapped through fills worse
    than -1R and has to show up that way rather than being rounded to a clean
    1R loss.

    `half_off` is the state AT EXIT TIME: True means TP1 filled on an earlier
    bar, so only half the position remained for the final fill.
    """
    def r_of(px: float) -> float:
        r = (px - trade.entry_px) / trade.risk
        return r if trade.side == "LONG" else -r

    tp1_fill = next((f for f in trade.fills if f.reason == "tp1"), None)
    final = trade.fills[-1]

    if half_off and tp1_fill is not None and final is not tp1_fill:
        trade.r_multiple = 0.5 * r_of(tp1_fill.px) + 0.5 * r_of(final.px)
    else:
        trade.r_multiple = r_of(final.px)


def run_brackets(bars: List[Bar], start_long: List[bool], start_short: List[bool],
                  atr_len: int = 14, sl_atr: float = 1.0, be_offset_atr: float = 0.15,
                  policy: Policy = "pessimistic",
                  stops: Optional[List[float]] = None,
                  targets: Optional[List[float]] = None) -> BracketReport:
    """One bracket at a time: a signal is ignored while a trade is open,
    matching the source indicator's own single-position behaviour (it can't
    fire a fresh startLongTrade while already long, since that requires the
    underlying `signal` to change).

    `stops` / `targets` are optional per-bar lists of explicit price levels,
    parallel to start_long/start_short. Supply them for structure-based
    strategies (SupplyDemandStructure does); omit them and stops fall back to
    sl_atr x ATR with TP1/TP2 at 1R/2R, unchanged."""
    atr_series = atr(bars, atr_len)
    trades: List[BracketTrade] = []
    i = 0
    n = len(bars)
    while i < n:
        side = "LONG" if (i < len(start_long) and start_long[i]) else (
               "SHORT" if (i < len(start_short) and start_short[i]) else None)
        if side is None:
            i += 1
            continue
        a = atr_series[i]
        if a != a or a <= 0:  # NaN or degenerate during warmup
            i += 1
            continue
        sp = stops[i] if (stops is not None and i < len(stops)) else None
        tg = targets[i] if (targets is not None and i < len(targets)) else None
        trade = _build_bracket(bars, i, side, a, sl_atr, be_offset_atr, sp, tg)
        if trade.risk <= 0:   # degenerate structural stop at the entry price
            i += 1
            continue
        _resolve(trade, bars, be_offset_atr, a, policy)
        trades.append(trade)
        i = trade.exit_i + 1  # no overlapping brackets
    return BracketReport(trades=trades, policy=policy)


def run_both_policies(bars: List[Bar], start_long: List[bool], start_short: List[bool],
                       atr_len: int = 14, sl_atr: float = 1.0,
                       be_offset_atr: float = 0.15,
                       stops: Optional[List[float]] = None,
                       targets: Optional[List[float]] = None
                       ) -> tuple[BracketReport, BracketReport]:
    """Convenience: run pessimistic and optimistic side by side. The gap
    between them bounds how much of any headline number is fill-order
    guesswork versus real edge."""
    pess = run_brackets(bars, start_long, start_short, atr_len, sl_atr, be_offset_atr,
                        "pessimistic", stops, targets)
    opt = run_brackets(bars, start_long, start_short, atr_len, sl_atr, be_offset_atr,
                       "optimistic", stops, targets)
    return pess, opt
