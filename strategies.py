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

import datetime
import math

from engine import (
    Strategy, Bar, sma, ema, rsi, atr, bollinger, highest, lowest,
    macd, stochastic, vwap_rolling, supertrend, roc,
    wavetrend, cci, adx, normalize01,
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


class OpeningRangeBreakout(Strategy):
    name = "Opening Range Breakout"
    description = ("Long/short breakout of the first N bars' range each session; "
                    "flat by session close. Needs intraday bars -- daily data has "
                    "only 1 bar/session, so the range degenerates to nothing useful.")

    def prepare(self):
        p = self.params
        n = p.get("range_bars", 6)
        bars = self.bars
        self.flatten_eod = p.get("flatten_eod", True)
        self.day_of = [datetime.datetime.utcfromtimestamp(b.t).date() for b in bars]

        self.or_high = [float("nan")] * len(bars)
        self.or_low = [float("nan")] * len(bars)
        self.in_range = [False] * len(bars)
        i = 0
        while i < len(bars):
            j = i
            while j < len(bars) and self.day_of[j] == self.day_of[i]:
                j += 1
            range_end = min(i + n, j)
            hh = max(b.h for b in bars[i:range_end])
            ll = min(b.l for b in bars[i:range_end])
            for k in range(i, j):
                self.or_high[k] = hh
                self.or_low[k] = ll
                if k < range_end:
                    self.in_range[k] = True
            i = j
        self.holding = "FLAT"

    def decide(self, i):
        if i == 0 or self.day_of[i] != self.day_of[i - 1]:
            self.holding = "FLAT"
        if not self.in_range[i]:
            c = self.closes[i]
            if c > self.or_high[i]:
                self.holding = "LONG"
            elif c < self.or_low[i]:
                self.holding = "SHORT"
        is_last_bar_of_day = (i == len(self.bars) - 1) or (self.day_of[i + 1] != self.day_of[i])
        if self.flatten_eod and is_last_bar_of_day:
            self.holding = "FLAT"
        return self.holding

    def to_pine(self):
        p = self.params
        n = p.get("range_bars", 6)
        return (f"//@version=6\n"
                f"indicator(\"Opening Range Breakout (free)\", overlay=true)\n"
                f"// Bar-count range (first {n} bars/session) translated to Pine's session\n"
                f"// tools -- for intraday charts, set these to your session's open.\n"
                f"rangeBars = {n}\n"
                f"newDay = ta.change(time(\"D\"))\n"
                f"var float orHigh = na\n"
                f"var float orLow = na\n"
                f"var int barsIn = 0\n"
                f"if newDay\n"
                f"    orHigh := high\n"
                f"    orLow := low\n"
                f"    barsIn := 0\n"
                f"else\n"
                f"    barsIn += 1\n"
                f"    if barsIn < rangeBars\n"
                f"        orHigh := math.max(orHigh, high)\n"
                f"        orLow := math.min(orLow, low)\n"
                f"inRange = barsIn < rangeBars\n"
                f"longCond = not inRange and close > orHigh\n"
                f"shortCond = not inRange and close < orLow\n"
                f"plot(orHigh, color=color.green)\nplot(orLow, color=color.red)\n"
                f"plotshape(longCond, style=shape.triangleup, color=color.green)\n"
                f"plotshape(shortCond, style=shape.triangledown, color=color.red)\n")


class BollingerRSI(Strategy):
    name = "Bollinger Bands + RSI"
    description = "Long when close is below the lower band while RSI is oversold; exit when RSI turns overbought or close breaks back above the upper band."

    def prepare(self):
        p = self.params
        self.lo, self.mid, self.hi = bollinger(self.closes, p.get("n", 20), p.get("k", 2.0))
        self.r = rsi(self.closes, p.get("rsi_n", 14))
        self.ov = p.get("oversold", 30)
        self.ob = p.get("overbought", 70)
        self.holding = False

    def decide(self, i):
        lo, hi, r = self.lo[i], self.hi[i], self.r[i]
        if lo != lo or r != r:  # warmup
            return "LONG" if self.holding else "FLAT"
        c = self.closes[i]
        if c < lo and r < self.ov:
            self.holding = True
        elif r > self.ob or c > hi:
            self.holding = False
        return "LONG" if self.holding else "FLAT"

    def to_pine(self):
        p = self.params
        n, k = p.get("n", 20), p.get("k", 2.0)
        rn, ov, ob = p.get("rsi_n", 14), p.get("oversold", 30), p.get("overbought", 70)
        return (f"//@version=6\n"
                f"indicator(\"Bollinger + RSI (free)\")\n"
                f"basis = ta.sma(close, {n})\n"
                f"dev = ta.stdev(close, {n}) * {k}\n"
                f"upper = basis + dev\n"
                f"lower = basis - dev\n"
                f"r = ta.rsi(close, {rn})\n"
                f"longCond = close < lower and r < {ov}\n"
                f"exitCond = r > {ob} or close > upper\n"
                f"plot(upper, color=color.gray)\n"
                f"plot(lower, color=color.gray)\n"
                f"plotshape(longCond, style=shape.triangleup, color=color.green)\n"
                f"plotshape(exitCond, style=shape.circledot, color=color.red)\n")


class LorentzianClassification(Strategy):
    name = "Lorentzian Classification"
    description = ("ML: KNN over Lorentzian distance across RSI/WaveTrend/CCI/ADX features, "
                    "filtered by regime + volatility + kernel-regression trend. Port of "
                    "jdehorty's public indicator (see lorentzian_classification.pine).")

    def prepare(self):
        p = self.params
        bars, closes, n = self.bars, self.closes, len(self.bars)
        neighbors_count = p.get("neighbors", 8)
        max_bars_back = p.get("max_bars_back", 2000)

        # Feature Engineering: the reference indicator's default 5-feature recipe
        f1 = [x / 100.0 if x == x else float("nan") for x in rsi(closes, 14)]
        wt1, wt2 = wavetrend(closes, 10, 11)
        f2 = normalize01([a - b if a == a and b == b else float("nan") for a, b in zip(wt1, wt2)])
        f3 = normalize01(cci(bars, 20))
        f4 = [x / 100.0 if x == x else float("nan") for x in adx(bars, 20)]
        f5 = [x / 100.0 if x == x else float("nan") for x in rsi(closes, 9)]
        feats = list(zip(f1, f2, f3, f4, f5))

        def lorentzian_dist(a, b):
            return sum(math.log1p(abs(x - y)) for x, y in zip(a, b))

        # Training labels: sign of the trailing 4-bar move, exactly as the
        # indicator computes it live (see lorentzian_classification.pine:335)
        labels = [0] * n
        for i in range(4, n):
            labels[i] = 1 if closes[i - 4] < closes[i] else (-1 if closes[i - 4] > closes[i] else 0)

        max_bars_back_index = n - 1 - max_bars_back if n - 1 >= max_bars_back else 0
        start_index = max_bars_back_index  # includeFullHistory=False (indicator default)

        # Approximate Nearest Neighbors search, Lorentzian distance.
        # ponytail: O(n^2) full rescan per bar, same cost as the source indicator's
        # per-bar loop over [startIndex..bar_index]; fine under a few thousand bars,
        # switch to a spatial index (k-d tree) if max_bars_back needs to grow a lot.
        predictions_sum = [0] * n
        for j in range(max_bars_back_index, n):
            if any(v != v for v in feats[j]):
                continue
            last_distance = -1.0
            distances: List[float] = []
            preds: List[int] = []
            for i in range(start_index, j + 1):
                if any(v != v for v in feats[i]):
                    continue
                d = lorentzian_dist(feats[j], feats[i])
                if d >= last_distance and i % 4 != 0:
                    last_distance = d
                    distances.append(d)
                    preds.append(labels[i])
                    if len(preds) > neighbors_count:
                        last_distance = distances[round(neighbors_count * 3 / 4)]
                        distances.pop(0)
                        preds.pop(0)
            predictions_sum[j] = sum(preds)

        # Filters: volatility (short ATR > long ATR) + regime (Kalman-like slope filter)
        atr_short, atr_long = atr(bars, 1), atr(bars, 10)
        vol_ok = [(s == s and l == l and s > l) for s, l in zip(atr_short, atr_long)]

        regime_threshold = p.get("regime_threshold", -0.1)
        ohlc4 = [(b.o + b.h + b.l + b.c) / 4.0 for b in bars]
        value1 = value2 = klmf = 0.0
        abs_slope = [0.0] * n
        for i in range(n):
            prev_src = ohlc4[i - 1] if i > 0 else ohlc4[i]
            value1 = 0.2 * (ohlc4[i] - prev_src) + 0.8 * value1
            value2 = 0.1 * (bars[i].h - bars[i].l) + 0.8 * value2
            omega = abs(value1 / value2) if value2 else 0.0
            alpha = (-omega ** 2 + math.sqrt(omega ** 4 + 16 * omega ** 2)) / 8
            prev_klmf = klmf
            klmf = alpha * ohlc4[i] + (1 - alpha) * prev_klmf
            abs_slope[i] = abs(klmf - prev_klmf)
        avg_slope = ema(abs_slope, 200)
        regime_ok = []
        for i in range(n):
            a = avg_slope[i]
            regime_ok.append(True if (a != a or a == 0) else (abs_slope[i] - a) / a >= regime_threshold)

        filter_all = [v and r for v, r in zip(vol_ok, regime_ok)]

        # Signal: sticky state, flips only on a filtered non-zero prediction
        signal = [0] * n
        s = 0
        for i in range(n):
            if predictions_sum[i] > 0 and filter_all[i]:
                s = 1
            elif predictions_sum[i] < 0 and filter_all[i]:
                s = -1
            signal[i] = s

        # Kernel regression (Nadaraya-Watson, rational quadratic) trend filter
        h, r, x_start = p.get("kernel_h", 8), p.get("kernel_r", 8.0), p.get("kernel_x", 25)
        window = min(n, 300)  # weights decay ~i^2, bars beyond this are negligible
        is_bullish_rate = [False] * n
        is_bearish_rate = [False] * n
        prev_yhat = float("nan")
        for j in range(n):
            if j < x_start:
                continue
            cw = tw = 0.0
            for i in range(min(j + 1, window)):
                w = (1 + (i * i) / (h * h * 2 * r)) ** (-r)
                cw += closes[j - i] * w
                tw += w
            yhat = cw / tw if tw else float("nan")
            if prev_yhat == prev_yhat and yhat == yhat:
                is_bullish_rate[j] = prev_yhat < yhat
                is_bearish_rate[j] = prev_yhat > yhat
            prev_yhat = yhat

        # Entries: a fresh signal flip that agrees with the kernel's direction
        start_long = [False] * n
        start_short = [False] * n
        for i in range(1, n):
            different = signal[i] != signal[i - 1]
            if signal[i] == 1 and different and is_bullish_rate[i]:
                start_long[i] = True
            elif signal[i] == -1 and different and is_bearish_rate[i]:
                start_short[i] = True

        # Exits: strict 4-bar hold (indicator default, useDynamicExits=False)
        end_long = [False] * n
        end_short = [False] * n
        bars_held = 0
        for i in range(1, n):
            different = signal[i] != signal[i - 1]
            bars_held = 0 if different else bars_held + 1
            held4 = bars_held == 4
            held_lt4 = 0 < bars_held < 4
            if i >= 4:
                last_was_buy = signal[i - 4] == 1
                last_was_sell = signal[i - 4] == -1
                if ((held4 and last_was_buy) or (held_lt4 and signal[i] == -1 and different and last_was_buy)) and start_long[i - 4]:
                    end_long[i] = True
                if ((held4 and last_was_sell) or (held_lt4 and signal[i] == 1 and different and last_was_sell)) and start_short[i - 4]:
                    end_short[i] = True

        # Collapse start/end events into a running position for decide(i)
        positions = ["FLAT"] * n
        pos = "FLAT"
        for i in range(n):
            if pos == "LONG" and end_long[i]:
                pos = "FLAT"
            if pos == "SHORT" and end_short[i]:
                pos = "FLAT"
            if start_long[i]:
                pos = "LONG"
            elif start_short[i]:
                pos = "SHORT"
            positions[i] = pos
        self.positions = positions

    def decide(self, i):
        return self.positions[i]

    def to_pine(self):
        return ("// Full Pine Script v6 source: see lorentzian_classification.pine\n"
                "// in the repo root (jdehorty's public indicator, MPL 2.0).\n")


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
    "bollrsi": BollingerRSI,
    "orb": OpeningRangeBreakout,
    "lorentzian": LorentzianClassification,
}


def list_strategies() -> str:
    lines = ["Available built-in strategies:"]
    for k, v in REGISTRY.items():
        lines.append(f"  - {k:9s} {v.name}: {v.description}")
    return "\n".join(lines)
