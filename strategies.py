"""
strategies.py - Ready-made strategies mirroring common public trading
concepts (the kind documented in the LuxAlgo "library" / classic TA).

Each is a self-contained `Strategy`. Add your own by subclassing.

Note on position handling: `decide(i)` returns the TARGET posture for bar
i. The engine only acts on transitions, so to HOLD a position the strategy
keeps returning the same posture. We track `self.holding` so a mean-reversion
or breakout signal stays on until its exit condition actually fires.
"""
from __future__ import annotations

from engine import (
    Strategy, Bar, sma, ema, rsi, atr, bollinger, highest, lowest,
    macd, stochastic, vwap_rolling, supertrend, roc,
)
from typing import List


class SMACrossover(Strategy):
    name = "SMA Crossover"
    description = "Long when fast SMA > slow SMA, flat otherwise."

    def prepare(self):
        p = self.params
        self.fast = sma(self.closes, p.get("fast", 20))
        self.slow = sma(self.closes, p.get("slow", 50))

    def decide(self, i):
        f, s = self.fast[i], self.slow[i]
        if f != f or s != s:
            return "FLAT"
        return "LONG" if f > s else "FLAT"

    def to_pine(self):
        p = self.params
        return (f"//@version=6\n"
                f"indicator(\"SMA Crossover (free)\")\n"
                f"fast = ta.sma(close, {p.get('fast',20)})\n"
                f"slow = ta.sma(close, {p.get('slow',50)})\n"
                f"longCond = fast > slow\n"
                f"plotshape(longCond, style=shape.triangleup)\n")


class RSIMeanReversion(Strategy):
    name = "RSI Mean Reversion"
    description = "Long when RSI < oversold, hold until RSI > overbought."

    def prepare(self):
        p = self.params
        self.r = rsi(self.closes, p.get("n", 14))
        self.ov = p.get("oversold", 30)
        self.ob = p.get("overbought", 70)
        self.holding = False

    def decide(self, i):
        r = self.r[i]
        if r != r:  # nan during warmup
            return "LONG" if self.holding else "FLAT"
        if r < self.ov:
            self.holding = True
        elif r > self.ob:
            self.holding = False
        return "LONG" if self.holding else "FLAT"

    def to_pine(self):
        p = self.params
        ov = p.get("oversold", 30)
        ob = p.get("overbought", 70)
        return (f"//@version=6\n"
                f"indicator(\"RSI Mean Reversion (free)\")\n"
                f"rsiLen = {p.get('n',14)}\n"
                f"ov = {ov}\n"
                f"ob = {ob}\n"
                f"r = ta.rsi(close, rsiLen)\n"
                f"longCond = ta.crossunder(r, ov)\n"
                f"exitCond = ta.crossover(r, ob)\n"
                f"plotshape(longCond, style=shape.triangleup, color=color.green)\n"
                f"plotshape(exitCond, style=shape.circledot, color=color.red)\n")


class BollingerBreakout(Strategy):
    name = "Bollinger Breakout"
    description = "Long on close above upper band, hold until close < lower band."

    def prepare(self):
        p = self.params
        lo, mid, hi = bollinger(self.closes, p.get("n", 20), p.get("k", 2.0))
        self.lo, self.hi, self.mid = lo, hi, mid
        self.holding = False

    def decide(self, i):
        c = self.closes[i]
        hi = self.hi[i]
        lo = self.lo[i]
        if hi != hi:  # warmup
            return "LONG" if self.holding else "FLAT"
        if c > hi:
            self.holding = True
        elif c < lo:
            self.holding = False
        return "LONG" if self.holding else "FLAT"

    def to_pine(self):
        p = self.params
        return (f"//@version=6\n"
                f"indicator(\"Bollinger Breakout (free)\")\n"
                f"len = {p.get('n',20)}\n"
                f"mult = {p.get('k',2.0)}\n"
                f"basis = ta.sma(close, len)\n"
                f"dev = ta.stdev(close, len) * mult\n"
                f"upper = basis + dev\n"
                f"lower = basis - dev\n"
                f"longCond = ta.crossover(close, upper)\n"
                f"exitCond = ta.crossunder(close, lower)\n")


class ATRTrend(Strategy):
    name = "ATR Trend Filter"
    description = "Long while price holds above the SMA; optional ATR filter."

    def prepare(self):
        p = self.params
        self.ma = sma(self.closes, p.get("n", 50))
        self.atrn = p.get("atrn", 14)
        self.holding = False

    def decide(self, i):
        if self.ma[i] != self.ma[i]:
            return "LONG" if self.holding else "FLAT"
        up = self.closes[i] > self.ma[i]
        if up:
            self.holding = True
        else:
            self.holding = False
        return "LONG" if self.holding else "FLAT"

    def to_pine(self):
        p = self.params
        return (f"//@version=6\n"
                f"indicator(\"ATR Trend Filter (free)\")\n"
                f"len = {p.get('n',50)}\n"
                f"ma = ta.sma(close, len)\n"
                f"longCond = close > ma\n"
                f"plot(ma, color=color.orange)\n")


class BreakoutN(Strategy):
    name = "N-Bar Breakout"
    description = "Donchian: long when close > N-bar high, exit on N-bar low."

    def prepare(self):
        p = self.params
        self.n = p.get("n", 20)
        highs = [b.h for b in self.bars]
        lows = [b.l for b in self.bars]
        self.hh = highest(highs, self.n)
        self.ll = lowest(lows, self.n)
        self.holding = False

    def decide(self, i):
        if self.hh[i] != self.hh[i]:  # warmup
            return "LONG" if self.holding else "FLAT"
        c = self.closes[i]
        if c > self.hh[i - 1]:
            self.holding = True
        elif c < self.ll[i - 1]:
            self.holding = False
        return "LONG" if self.holding else "FLAT"

    def to_pine(self):
        n = self.params.get("n", 20)
        return (f"//@version=6\n"
                f"indicator(\"Donchian Breakout (free)\")\n"
                f"len = {n}\n"
                f"hh = ta.highest(high, len)\n"
                f"ll = ta.lowest(low, len)\n"
                f"longCond = ta.crossover(close, hh[1])\n"
                f"exitCond = ta.crossunder(close, ll[1])\n"
                f"plot(hh, color=color.green)\n"
                f"plot(ll, color=color.red)\n")


class MACDCrossover(Strategy):
    name = "MACD Crossover"
    description = "Long while MACD line is above its signal line."

    def prepare(self):
        p = self.params
        line, sig, _ = macd(self.closes, p.get("fast", 12), p.get("slow", 26), p.get("signal", 9))
        self.line, self.sig = line, sig

    def decide(self, i):
        l, s = self.line[i], self.sig[i]
        if l != l or s != s:
            return "FLAT"
        return "LONG" if l > s else "FLAT"

    def to_pine(self):
        p = self.params
        return (f"//@version=6\n"
                f"indicator(\"MACD Crossover (free)\")\n"
                f"[macdLine, signalLine, _] = ta.macd(close, {p.get('fast',12)}, {p.get('slow',26)}, {p.get('signal',9)})\n"
                f"longCond = macdLine > signalLine\n"
                f"plotshape(longCond, style=shape.triangleup)\n")


class StochasticReversion(Strategy):
    name = "Stochastic Reversion"
    description = "Long when %K < oversold, hold until %K > overbought."

    def prepare(self):
        p = self.params
        self.k, self.d = stochastic(self.bars, p.get("n", 14), p.get("d", 3))
        self.ov = p.get("oversold", 20)
        self.ob = p.get("overbought", 80)
        self.holding = False

    def decide(self, i):
        k = self.k[i]
        if k != k:
            return "LONG" if self.holding else "FLAT"
        if k < self.ov:
            self.holding = True
        elif k > self.ob:
            self.holding = False
        return "LONG" if self.holding else "FLAT"

    def to_pine(self):
        p = self.params
        ov = p.get("oversold", 20)
        ob = p.get("overbought", 80)
        return (f"//@version=6\n"
                f"indicator(\"Stochastic Reversion (free)\")\n"
                f"k = ta.stoch(close, high, low, {p.get('n',14)})\n"
                f"longCond = ta.crossunder(k, {ov})\n"
                f"exitCond = ta.crossover(k, {ob})\n")


class VWAPReversion(Strategy):
    name = "Rolling VWAP Trend"
    description = "Long while close holds above the rolling VWAP."

    def prepare(self):
        p = self.params
        self.vwap = vwap_rolling(self.bars, p.get("n", 20))

    def decide(self, i):
        v = self.vwap[i]
        if v != v:
            return "FLAT"
        return "LONG" if self.closes[i] > v else "FLAT"

    def to_pine(self):
        n = self.params.get("n", 20)
        return (f"//@version=6\n"
                f"indicator(\"Rolling VWAP Trend (free)\")\n"
                f"src = (high+low+close)/3\n"
                f"vwap = math.sum(src*volume, {n}) / math.sum(volume, {n})\n"
                f"longCond = close > vwap\n"
                f"plot(vwap, color=color.blue)\n")


class SupertrendFollow(Strategy):
    name = "Supertrend Follow"
    description = "Long while Supertrend direction is up."

    def prepare(self):
        p = self.params
        _, self.direction = supertrend(self.bars, p.get("n", 10), p.get("mult", 3.0))

    def decide(self, i):
        d = self.direction[i]
        return "LONG" if d == 1 else "FLAT"

    def to_pine(self):
        p = self.params
        return (f"//@version=6\n"
                f"indicator(\"Supertrend Follow (free)\")\n"
                f"[line, dir] = ta.supertrend({p.get('mult',3.0)}, {p.get('n',10)})\n"
                f"longCond = dir < 0\n"
                f"plot(line, color=dir < 0 ? color.green : color.red)\n")


class MomentumROC(Strategy):
    name = "Momentum ROC"
    description = "Long while rate-of-change is positive."

    def prepare(self):
        p = self.params
        self.roc = roc(self.closes, p.get("n", 10))

    def decide(self, i):
        r = self.roc[i]
        if r != r:
            return "FLAT"
        return "LONG" if r > 0 else "FLAT"

    def to_pine(self):
        n = self.params.get("n", 10)
        return (f"//@version=6\n"
                f"indicator(\"Momentum ROC (free)\")\n"
                f"r = ta.roc(close, {n})\n"
                f"longCond = r > 0\n"
                f"plot(r, color=color.purple)\n")


REGISTRY = {
    "sma": SMACrossover,
    "rsi": RSIMeanReversion,
    "boll": BollingerBreakout,
    "atr": ATRTrend,
    "donchian": BreakoutN,
    "macd": MACDCrossover,
    "stoch": StochasticReversion,
    "vwap": VWAPReversion,
    "supertrend": SupertrendFollow,
    "roc": MomentumROC,
}


def list_strategies() -> str:
    lines = ["Available built-in strategies:"]
    for k, v in REGISTRY.items():
        lines.append(f"  - {k:9s} {v.name}: {v.description}")
    return "\n".join(lines)
