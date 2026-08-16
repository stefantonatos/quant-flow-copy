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
    wavetrend, cci, adx, normalize01, pivots, confirmed_pivots,
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


class PowerOfThree(Strategy):
    name = "Power of 3 (AMD)"
    description = ("ICT-style Accumulation/Manipulation/Distribution: mark the Asian "
                    "session's high/low, wait for a London-session liquidity sweep of "
                    "one side of that range followed by a close back inside (the "
                    "reversal signal), then trade that direction with a fixed R:R "
                    "target through the NY session. One trade per day. Needs intraday "
                    "bars with UTC timestamps -- see the class docstring caveat below.")
    # Simplification vs. a real bracket order: this engine has no intrabar fills, so
    # stop/target touches are approximated by checking each bar's high/low against the
    # levels and flattening at that bar's close -- not a live-accurate fill price. The
    # MT5 port (mt5/PowerOfThree.mq5) places real SL/TP orders instead; this Python
    # version is for quick screening/optimization only.

    def prepare(self):
        p = self.params
        bars = self.bars
        n = len(bars)
        asia_start = p.get("asia_start_hour", 0)
        asia_end = p.get("asia_end_hour", 8)
        manip_end = p.get("manip_end_hour", 13)
        session_end = p.get("session_end_hour", 21)
        rr = p.get("risk_reward", 2.0)

        hours = [datetime.datetime.utcfromtimestamp(b.t).hour for b in bars]
        days = [datetime.datetime.utcfromtimestamp(b.t).date() for b in bars]
        self.positions = ["FLAT"] * n

        i = 0
        while i < n:
            j = i
            while j < n and days[j] == days[i]:
                j += 1
            asia_idx = [k for k in range(i, j) if asia_start <= hours[k] < asia_end]
            if not asia_idx:
                i = j
                continue
            asia_high = max(bars[k].h for k in asia_idx)
            asia_low = min(bars[k].l for k in asia_idx)
            swept_high = swept_low = False
            holding = "FLAT"
            traded_today = False
            stop_px = target_px = None

            for k in range(i, j):
                h = hours[k]
                if holding == "FLAT" and not traded_today and asia_end <= h < manip_end:
                    if bars[k].h > asia_high:
                        swept_high = True
                    if bars[k].l < asia_low:
                        swept_low = True
                    if swept_high and bars[k].c < asia_high:
                        holding = "SHORT"
                        traded_today = True
                        entry = bars[k].c
                        stop_px = max(bars[m].h for m in range(i, k + 1) if hours[m] < manip_end)
                        target_px = entry - rr * (stop_px - entry)
                    elif swept_low and bars[k].c > asia_low:
                        holding = "LONG"
                        traded_today = True
                        entry = bars[k].c
                        stop_px = min(bars[m].l for m in range(i, k + 1) if hours[m] < manip_end)
                        target_px = entry + rr * (entry - stop_px)
                elif holding == "LONG":
                    if bars[k].l <= stop_px or bars[k].h >= target_px:
                        holding = "FLAT"
                elif holding == "SHORT":
                    if bars[k].h >= stop_px or bars[k].l <= target_px:
                        holding = "FLAT"

                if h >= session_end:
                    holding = "FLAT"
                self.positions[k] = holding
            i = j

    def decide(self, i):
        return self.positions[i]

    def to_pine(self):
        p = self.params
        return (f"//@version=6\n"
                f"indicator(\"Power of 3 / AMD (free)\", overlay=true)\n"
                f"// Session hours below are UTC -- adjust for your chart's timezone.\n"
                f"asiaStart = {p.get('asia_start_hour', 0)}\n"
                f"asiaEnd = {p.get('asia_end_hour', 8)}\n"
                f"manipEnd = {p.get('manip_end_hour', 13)}\n"
                f"h = hour(time, \"UTC\")\n"
                f"newDay = ta.change(dayofmonth(time, \"UTC\"))\n"
                f"var float asiaHigh = na\nvar float asiaLow = na\nvar bool sweptHigh = false\nvar bool sweptLow = false\nvar bool tradedToday = false\n"
                f"if newDay\n    asiaHigh := na\n    asiaLow := na\n    sweptHigh := false\n    sweptLow := false\n    tradedToday := false\n"
                f"inAsia = h >= asiaStart and h < asiaEnd\n"
                f"if inAsia\n    asiaHigh := na(asiaHigh) ? high : math.max(asiaHigh, high)\n    asiaLow := na(asiaLow) ? low : math.min(asiaLow, low)\n"
                f"inManip = h >= asiaEnd and h < manipEnd and not tradedToday and not na(asiaHigh)\n"
                f"if inManip\n"
                f"    sweptHigh := sweptHigh or high > asiaHigh\n"
                f"    sweptLow := sweptLow or low < asiaLow\n"
                f"longCond = inManip and sweptLow and close > asiaLow\n"
                f"shortCond = inManip and sweptHigh and close < asiaHigh\n"
                f"if longCond or shortCond\n    tradedToday := true\n"
                f"plot(asiaHigh, color=color.orange)\nplot(asiaLow, color=color.orange)\n"
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

        # Training labels: the trailing 4-bar move, labelled AGAINST its
        # direction, exactly as the indicator does (lorentzian_classification.pine:335):
        #
        #   y_train_series = src[4] < src[0] ? direction.short
        #                  : src[4] > src[0] ? direction.long
        #                  : direction.neutral
        #   with direction.long = 1, direction.short = -1   (.pine:291-296)
        #
        # In Pine `src[4]` is 4 bars AGO and `src[0]` is NOW, so `src[4] < src[0]`
        # means price ROSE -- and upstream labels that SHORT (-1). A 4-bar FALL is
        # labelled LONG (+1). That looks backwards, and it is deliberate on
        # upstream's part: the model fades the trailing move rather than
        # extrapolating it. Do not "fix" the sign to read naturally.
        #
        # This previously read `1 if closes[i-4] < closes[i] else -1`, which
        # inverted every label and made this port trade the exact mirror image of
        # the indicator it replicates. See test_lorentzian_labels_match_pine.
        labels = [0] * n
        for i in range(4, n):
            labels[i] = -1 if closes[i - 4] < closes[i] else (1 if closes[i - 4] > closes[i] else 0)

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

        # Exposed so the Pine-parity test can assert the label signs directly
        # rather than inferring them from downstream behaviour.
        self.labels = labels

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

        # Exposed for brackets.py: engine.run() only sees the collapsed
        # posture above, but a bracket backtest needs the actual entry
        # events -- posture alone can't tell a fresh signal from a bar where
        # the prior position simply held.
        self.start_long = start_long
        self.start_short = start_short

    def decide(self, i):
        return self.positions[i]

    def to_pine(self):
        return ("// Full Pine Script v6 source: see lorentzian_classification.pine\n"
                "// in the repo root (jdehorty's public indicator, MPL 2.0).\n")


class SupplyDemandStructure(Strategy):
    """TradingLab's "The Only Trading Strategy You'll Ever Need" (2024-11-04).

    Three steps, per the video:
      1. Market structure -- uptrend = higher highs AND higher lows; downtrend
         = lower lows AND lower highs. Trade only with the trend.
      2. Supply/demand zone -- in an uptrend mark the demand area where price
         consolidated before shooting up, and wait for price to return to it.
         Mirrored for supply in downtrends.
      3. Risk:reward -- take the trade only if R:R >= 2.5, else skip it. The
         video credits this single filter with most of the strategy's edge.

    CAVEAT ON FIDELITY. The video is not reachable from this container
    (youtube.com and every transcript mirror are egress-blocked), so these
    rules were reconstructed from search-index summaries, not the transcript.
    More importantly, "where price consolidated before shooting up" is a
    human eyeballing a chart -- it is not a rule. The impulse/base detection
    below is *a* defensible mechanization, not *the* strategy, and results
    will move with `impulse_atr` and `base_max_bars`. Treat any backtest of
    this as a test of this interpretation.

    Exposes start_long/start_short plus stops/targets so brackets.py can
    execute the real structural levels rather than re-deriving from ATR.
    """

    name = "Supply/Demand + Structure"
    description = ("Price action: trade with market structure (HH/HL or LL/LH) off "
                    "demand/supply zones, filtered to setups offering at least "
                    "2.5:1 reward-to-risk. Mechanization of TradingLab's video "
                    "strategy -- zone-drawing is discretionary, see class docstring.")

    def prepare(self):
        p = self.params
        bars, n = self.bars, len(self.bars)
        left = right = p.get("pivot_lookback", 5)
        impulse_atr = p.get("impulse_atr", 2.0)
        impulse_max_bars = p.get("impulse_max_bars", 5)
        base_max_bars = p.get("base_max_bars", 3)
        zone_max_age = p.get("zone_max_age", 200)
        sl_buffer_atr = p.get("sl_buffer_atr", 0.25)
        self.min_rr = p.get("min_rr", 2.5)

        a = atr(bars, p.get("atr_n", 14))
        ph_events, pl_events = confirmed_pivots(bars, left, right)

        start_long = [False] * n
        start_short = [False] * n
        stops: List[float] = [float("nan")] * n
        targets: List[float] = [float("nan")] * n

        # Pivot events keyed by the bar they become knowable on, so nothing
        # below can consult a swing before its right-hand bars have closed.
        ph_by_confirm = {}
        pl_by_confirm = {}
        for c, idx, px in ph_events:
            ph_by_confirm.setdefault(c, []).append((idx, px))
        for c, idx, px in pl_events:
            pl_by_confirm.setdefault(c, []).append((idx, px))

        swing_highs: List[tuple] = []   # (bar_index, price), confirmed only
        swing_lows: List[tuple] = []
        zones: List[dict] = []          # active demand/supply zones

        for i in range(n):
            for idx, px in ph_by_confirm.get(i, []):
                swing_highs.append((idx, px))
            for idx, px in pl_by_confirm.get(i, []):
                swing_lows.append((idx, px))

            # --- Step 1: market structure ---------------------------------
            trend = 0
            if len(swing_highs) >= 2 and len(swing_lows) >= 2:
                hh = swing_highs[-1][1] > swing_highs[-2][1]
                hl = swing_lows[-1][1] > swing_lows[-2][1]
                ll = swing_lows[-1][1] < swing_lows[-2][1]
                lh = swing_highs[-1][1] < swing_highs[-2][1]
                if hh and hl:
                    trend = 1
                elif ll and lh:
                    trend = -1

            # --- Step 2: find new zones off completed impulse legs --------
            # An impulse is a fast directional move; the zone is the small
            # base immediately preceding it. Detected on bar i looking only
            # backwards, so it is causal.
            av = a[i]
            if av == av and av > 0 and i >= impulse_max_bars + base_max_bars:
                for span in range(1, impulse_max_bars + 1):
                    lo_i, hi_i = i - span, i
                    move = bars[hi_i].c - bars[lo_i].o
                    if abs(move) < impulse_atr * av:
                        continue
                    bstart = max(0, lo_i - base_max_bars)
                    bbars = bars[bstart:lo_i]
                    if not bbars:
                        continue
                    if move > 0:
                        zones.append({"kind": "demand", "top": max(b.h for b in bbars),
                                      "bot": min(b.l for b in bbars), "born": i})
                    else:
                        zones.append({"kind": "supply", "top": max(b.h for b in bbars),
                                      "bot": min(b.l for b in bbars), "born": i})
                    break

            # Expire zones: aged out, or price closed clean through them.
            zones = [z for z in zones
                     if i - z["born"] <= zone_max_age
                     and not (z["kind"] == "demand" and bars[i].c < z["bot"])
                     and not (z["kind"] == "supply" and bars[i].c > z["top"])]

            # --- Entry: price returns into a with-trend zone ---------------
            if trend == 0 or av != av or av <= 0:
                continue
            want = "demand" if trend == 1 else "supply"
            for z in zones:
                if z["kind"] != want or z["born"] >= i:
                    continue
                touched = (bars[i].l <= z["top"] if trend == 1
                           else bars[i].h >= z["bot"])
                if not touched:
                    continue

                entry = bars[i].c
                if trend == 1:
                    stop = z["bot"] - sl_buffer_atr * av
                    target = swing_highs[-1][1] if swing_highs else float("nan")
                    risk, reward = entry - stop, (target - entry)
                else:
                    stop = z["top"] + sl_buffer_atr * av
                    target = swing_lows[-1][1] if swing_lows else float("nan")
                    risk, reward = stop - entry, (entry - target)

                if risk <= 0 or reward != reward or reward <= 0:
                    continue
                # --- Step 3: the R:R filter -- the whole point of step 3 ---
                if reward / risk < self.min_rr:
                    continue

                if trend == 1:
                    start_long[i] = True
                else:
                    start_short[i] = True
                stops[i], targets[i] = stop, target
                zones.remove(z)
                break

        self.start_long = start_long
        self.start_short = start_short
        self.stops = stops
        self.targets = targets

        # Collapse into a posture series so engine.run() can consume it too.
        # Without brackets there is no stop/target, so this holds until the
        # opposite signal -- a strictly worse execution model than
        # `run.py --bracket`, which is the intended way to run this.
        positions = ["FLAT"] * n
        pos = "FLAT"
        for i in range(n):
            if start_long[i]:
                pos = "LONG"
            elif start_short[i]:
                pos = "SHORT"
            positions[i] = pos
        self.positions = positions

    def decide(self, i):
        return self.positions[i]

    def to_pine(self):
        p = self.params
        return (f"//@version=6\n"
                f"indicator(\"Supply/Demand + Structure (free)\", overlay=true)\n"
                f"lb = {p.get('pivot_lookback', 5)}\n"
                f"ph = ta.pivothigh(high, lb, lb)\n"
                f"pl = ta.pivotlow(low, lb, lb)\n"
                f"// Structure: uptrend = HH and HL; zones = base before an impulse leg;\n"
                f"// entry on return to zone, stop beyond it, target = last swing,\n"
                f"// taken only when reward/risk >= {p.get('min_rr', 2.5)}.\n"
                f"plotshape(not na(ph), style=shape.triangledown, location=location.abovebar)\n"
                f"plotshape(not na(pl), style=shape.triangleup, location=location.belowbar)\n")


class NoWickRetrace(Strategy):
    """@bardfx's "No Wick" strategy.

    Three rules, as he describes them:
      1. Mark a wickless candle WITH THE TREND -- a bullish candle with no
         bottom wick in an uptrend, a bearish candle with no top wick in a
         downtrend.
      2. Wait for price to retrace back to that candle.
      3. Enter with the trend, stop beyond structure, target roughly 1:1.

    "Wickless" is exact equality, not a judgement call: the indicator he uses
    (xGhozt Wickless Candles) marks a bar where low == min(open, close) or
    high == max(open, close). `wick_tol` (absolute price) defaults to 0.0 to
    match that exactly.

    TIMEFRAME/INSTRUMENT WARNING. Stefan is running this on 15-minute FOREX.
    Exact equality is a much stronger condition there than on a tick-sized
    future: EURUSD quotes to 5 decimals, so a 15m bar closing with a
    *precisely* zero wick is rare, and `wick_tol=0.0` may yield almost no
    setups. `wick_tol_frac` is the knob for that -- a wick counted as absent
    when it is <= that fraction of the bar's own range, which scales across
    instruments the way an absolute price tolerance cannot. It defaults to
    0.0 (strict) so nothing is loosened silently; raise it and re-measure,
    because loosening the definition is loosening the strategy, and the
    signal count will move a long way with it.

    The premise is at least internally coherent -- the wickless indicator's
    own author argues a missing wick tends to get filled later, and "price
    retraces to the candle" IS that wick forming. But note what follows:
    the entry level is one price has already demonstrated it returns to, so a
    high fill rate is built into the setup and says nothing about what happens
    after the fill.

    Two things in the description are not rules and had to be decided:
      - "the trend" -- built both ways, `trend_mode` selects swing structure
        (HH/HL, shared with SupplyDemandStructure) or a close-vs-EMA test.
      - "some breathing room" below the candle -- `stop_buffer_atr`.

    Be clear about what that second one does, because it is not a detail.
    Entry is AT the flat edge, which for a bullish candle is its low. So a
    stop "below the candle" is a stop below the entry by the buffer and
    nothing else: the buffer IS the risk, and since the target is rr x risk,
    it sets the whole trade's geometry. Too tight and the entry bar's own
    range straddles both the stop and the target, which shows up honestly
    here as a 100% ambiguous bracket -- the pessimistic and optimistic
    policies then disagree by 2R on every single trade, and neither number
    means anything. The 0.5 ATR default is a sane starting point, not a
    finding; it is in the opt.py grid because it has to be measured.

    Exposes start_long/start_short/entries/stops/targets. Entry is a RESTING
    LIMIT at the candle's flat edge, not the signal bar's close, so this must
    be run through `run.py --bracket` to mean anything.
    """

    name = "No Wick Retrace"
    description = ("Mark a with-trend candle that has no wick on its trend side, "
                    "wait for price to retrace to that flat edge, enter there with "
                    "a stop beyond it and a 1:1 target. Mechanization of @bardfx's "
                    "'no wick' setup -- see class docstring on what was decided.")

    def prepare(self):
        p = self.params
        bars, n = self.bars, len(self.bars)
        tol = p.get("wick_tol", 0.0)
        tol_frac = p.get("wick_tol_frac", 0.0)
        trend_mode = p.get("trend_mode", "structure")
        ema_len = p.get("ema_len", 50)
        lb = p.get("pivot_lookback", 5)
        stop_mode = p.get("stop_mode", "candle")
        buf = p.get("stop_buffer_atr", 0.50)
        rr = p.get("rr", 1.0)
        zone_max_age = p.get("zone_max_age", 100)

        a = atr(bars, p.get("atr_n", 14))
        e = ema(self.closes, ema_len)
        ph_events, pl_events = confirmed_pivots(bars, lb, lb)

        start_long = [False] * n
        start_short = [False] * n
        entries: List[float] = [float("nan")] * n
        stops: List[float] = [float("nan")] * n
        targets: List[float] = [float("nan")] * n

        ph_by_confirm, pl_by_confirm = {}, {}
        for c, idx, px in ph_events:
            ph_by_confirm.setdefault(c, []).append((idx, px))
        for c, idx, px in pl_events:
            pl_by_confirm.setdefault(c, []).append((idx, px))

        swing_highs: List[tuple] = []
        swing_lows: List[tuple] = []
        marks: List[dict] = []   # unfilled wickless levels waiting for a retrace
        trends = [0] * n

        # Detection is a property of the bar alone, so precompute it. A
        # bullish candle with a flat bottom (low == open) is the long setup;
        # a bearish candle with a flat top (high == open) is the short one.
        # A fully wickless bullish candle has a flat bottom too, so it counts.
        is_bull_mark = [False] * n
        is_bear_mark = [False] * n
        for i, b in enumerate(bars):
            # Effective tolerance: the looser of the absolute and the
            # range-relative one, so either knob alone does the job.
            lim = max(tol, tol_frac * (b.h - b.l))
            if b.c > b.o and b.o - b.l <= lim:
                is_bull_mark[i] = True
            elif b.c < b.o and b.h - b.o <= lim:
                is_bear_mark[i] = True
        # Exposed so a test can separate "the candle wasn't detected" from
        # "the trend gate rejected it" -- two very different failures.
        self.debug_bull_marks = is_bull_mark
        self.debug_bear_marks = is_bear_mark

        for i in range(n):
            for idx, px in ph_by_confirm.get(i, []):
                swing_highs.append((idx, px))
            for idx, px in pl_by_confirm.get(i, []):
                swing_lows.append((idx, px))

            # --- Trend ------------------------------------------------------
            trend = 0
            if trend_mode == "ema":
                ev = e[i]
                if ev == ev:
                    trend = 1 if bars[i].c > ev else (-1 if bars[i].c < ev else 0)
            else:
                if len(swing_highs) >= 2 and len(swing_lows) >= 2:
                    hh = swing_highs[-1][1] > swing_highs[-2][1]
                    hl = swing_lows[-1][1] > swing_lows[-2][1]
                    ll = swing_lows[-1][1] < swing_lows[-2][1]
                    lh = swing_highs[-1][1] < swing_highs[-2][1]
                    if hh and hl:
                        trend = 1
                    elif ll and lh:
                        trend = -1
            trends[i] = trend

            av = a[i]

            # --- Step 2: has price retraced into an existing mark? ----------
            # Checked BEFORE this bar can create a new mark, so a candle can
            # never trigger its own level on the bar that printed it.
            if trend != 0 and av == av and av > 0:
                for m in marks:
                    if m["dir"] != trend:
                        continue
                    # The retrace must reach the flat edge itself -- the low
                    # for a bullish candle, the high for a bearish one. Wicking
                    # into the body is not a fill; that level is the whole point.
                    level = m["level"]
                    touched = (bars[i].l <= level if trend == 1
                               else bars[i].h >= level)
                    if not touched:
                        continue

                    entry = level
                    if trend == 1:
                        base = (m["level"] if stop_mode == "candle"
                                else (swing_lows[-1][1] if swing_lows else m["level"]))
                        stop = min(base, m["level"]) - buf * av
                        risk = entry - stop
                        target = entry + rr * risk
                    else:
                        base = (m["level"] if stop_mode == "candle"
                                else (swing_highs[-1][1] if swing_highs else m["level"]))
                        stop = max(base, m["level"]) + buf * av
                        risk = stop - entry
                        target = entry - rr * risk
                    if risk <= 0:
                        continue

                    if trend == 1:
                        start_long[i] = True
                    else:
                        start_short[i] = True
                    entries[i], stops[i], targets[i] = entry, stop, target
                    marks.remove(m)
                    break

            # --- Step 1: mark a new with-trend wickless candle --------------
            if trend == 1 and is_bull_mark[i]:
                marks.append({"dir": 1, "level": bars[i].l, "born": i})
            elif trend == -1 and is_bear_mark[i]:
                marks.append({"dir": -1, "level": bars[i].h, "born": i})

            # Expire: aged out, or price closed clean through the level, which
            # means the "unfilled wick" thesis for that candle is dead.
            marks = [m for m in marks
                     if i - m["born"] <= zone_max_age
                     and not (m["dir"] == 1 and bars[i].c < m["level"])
                     and not (m["dir"] == -1 and bars[i].c > m["level"])]

        self.start_long = start_long
        self.start_short = start_short
        self.entries = entries
        self.stops = stops
        self.targets = targets
        self.allow_entry_bar_fill = True
        # bardfx describes ONE target at roughly 1:1, not a scale-out. Leaving
        # the default half-off at TP1 in place would score a win as 0.75R
        # against a 1R loss and quietly move breakeven to 57%.
        self.partial_at_tp1 = False
        self.trends = trends

        # Posture series so engine.run() can consume it too. Without brackets
        # there is no stop or 1:1 target, so this holds until the opposite
        # signal -- a materially different (and worse) strategy than the one
        # described. `run.py --bracket` is the intended path.
        positions = ["FLAT"] * n
        pos = "FLAT"
        for i in range(n):
            if start_long[i]:
                pos = "LONG"
            elif start_short[i]:
                pos = "SHORT"
            positions[i] = pos
        self.positions = positions

    def decide(self, i):
        return self.positions[i]

    def to_pine(self):
        p = self.params
        return (f"//@version=6\n"
                f"indicator(\"No Wick Retrace (free)\", overlay=true)\n"
                f"tol = {p.get('wick_tol', 0.0)}\n"
                f"bullMark = close > open and (open - low) <= tol\n"
                f"bearMark = close < open and (high - open) <= tol\n"
                f"// Mark the flat edge of a with-trend wickless candle, wait for\n"
                f"// price to retrace to it, enter there with a stop {p.get('stop_buffer_atr', 0.50)}x ATR\n"
                f"// beyond it and a {p.get('rr', 1.0)}:1 target.\n"
                f"plotshape(bullMark, style=shape.triangleup, location=location.belowbar)\n"
                f"plotshape(bearMark, style=shape.triangledown, location=location.abovebar)\n")


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
    "po3": PowerOfThree,
    "lorentzian": LorentzianClassification,
    "sdz": SupplyDemandStructure,
    "nowick": NoWickRetrace,
}


def list_strategies() -> str:
    lines = ["Available built-in strategies:"]
    for k, v in REGISTRY.items():
        lines.append(f"  - {k:9s} {v.name}: {v.description}")
    return "\n".join(lines)
