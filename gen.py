"""
gen.py - Plain-English -> strategy (the "Quant" equivalent).

You describe what you want; this parses the sentence for known trading
vocabulary, picks the closest built-in strategy, tunes its parameters,
and emits both a runnable backtest and a Pine Script equivalent.

This is a transparent keyword router, NOT a trained model -- so you can
see and edit exactly how it maps words to logic. (LuxAlgo's Quant uses a
proprietary trained model; this gives you the same loop, free and open.)
"""
from __future__ import annotations

import re
from typing import Tuple, Optional

from strategies import REGISTRY, list_strategies


_FIRST_INT = re.compile(r"(\d+)")


def _first_int(text: str, default: int) -> int:
    m = _FIRST_INT.search(text)
    return int(m.group(1)) if m else default


def plan_from_text(text: str) -> Tuple[str, dict, str]:
    """
    Returns (strategy_key, params, explanation).
    """
    t = text.lower()

    # ---- Opening Range Breakout (check before the generic "breakout" -> donchian match) ----
    if "orb" in t.split() or "opening range" in t:
        n = _first_int(t, 6)
        params = {"range_bars": n, "flatten_eod": True}
        expl = (f"Matched OPENING RANGE BREAKOUT (first {n} bars/session). "
                f"Long above the opening range high, short below the opening range low, flat by session close. "
                f"Needs intraday bars to mean anything.")
        return "orb", params, expl

    # ---- Bollinger Bands + RSI combo ----
    if ("bollinger" in t or "band" in t) and ("rsi" in t or "oversold" in t or "overbought" in t):
        n = _first_int(t, 20)
        rn = 14
        m = re.search(r"rsi.*?(\d+)", t)
        if m:
            rn = int(m.group(1))
        ov, ob = 30, 70
        m = re.search(r"oversold.*?(\d+)", t)
        if m:
            ov = int(m.group(1))
        m = re.search(r"overbought.*?(\d+)", t)
        if m:
            ob = int(m.group(1))
        params = {"n": n, "rsi_n": rn, "oversold": ov, "overbought": ob}
        expl = (f"Matched BOLLINGER + RSI (band {n}, RSI {rn}). "
                f"Long when close < lower band and RSI < {ov}; exit when RSI > {ob} or close > upper band.")
        return "bollrsi", params, expl

    # ---- Lorentzian Classification (ML / KNN) ----
    if "lorentzian" in t or "knn" in t or "k-nearest" in t or "machine learning" in t:
        params = {"neighbors": 8, "max_bars_back": 2000}
        expl = "Matched LORENTZIAN CLASSIFICATION. KNN over RSI/WaveTrend/CCI/ADX with a kernel-regression trend filter."
        return "lorentzian", params, expl

    # ---- MACD ----
    if "macd" in t:
        params = {"fast": 12, "slow": 26, "signal": 9}
        expl = "Matched MACD CROSSOVER. Long while MACD line is above its signal line."
        return "macd", params, expl

    # ---- Stochastic ----
    if "stochastic" in t or "stoch" in t:
        n = _first_int(t, 14)
        params = {"n": n, "oversold": 20, "overbought": 80}
        expl = f"Matched STOCHASTIC REVERSION (period {n}). Long when %K < 20, exits when %K > 80."
        return "stoch", params, expl

    # ---- VWAP ----
    if "vwap" in t:
        n = _first_int(t, 20)
        params = {"n": n}
        expl = f"Matched ROLLING VWAP TREND (period {n}). Long while close holds above VWAP."
        return "vwap", params, expl

    # ---- Supertrend ----
    if "supertrend" in t or "super trend" in t:
        n = _first_int(t, 10)
        params = {"n": n, "mult": 3.0}
        expl = f"Matched SUPERTREND FOLLOW (ATR period {n}, mult 3.0). Long while direction is up."
        return "supertrend", params, expl

    # ---- Rate of change / momentum ROC ----
    if "rate of change" in t or " roc" in f" {t}":
        n = _first_int(t, 10)
        params = {"n": n}
        expl = f"Matched MOMENTUM ROC (period {n}). Long while rate-of-change is positive."
        return "roc", params, expl

    # ---- RSI / mean reversion ----
    if "rsi" in t or "oversold" in t or "overbought" in t or "mean reversion" in t:
        n = _first_int(t, 14)
        ov = 30
        ob = 70
        m = re.search(r"oversold.*?(\d+)", t)
        if m:
            ov = int(m.group(1))
        m = re.search(r"overbought.*?(\d+)", t)
        if m:
            ob = int(m.group(1))
        params = {"n": n, "oversold": ov, "overbought": ob}
        expl = (f"Matched MEAN REVERSION via RSI({n}). "
                f"Goes long when RSI < {ov}, exits when RSI > {ob}.")
        return "rsi", params, expl

    # ---- Bollinger ----
    if "bollinger" in t or "band" in t:
        n = _first_int(t, 20)
        k = 2.0
        m = re.search(r"(\d+\.?\d*)\s*std", t)
        if m:
            k = float(m.group(1))
        params = {"n": n, "k": k}
        expl = (f"Matched BOLLINGER BREAKOUT (period {n}, {k} std). "
                f"Long on close above upper band, flat on close below lower band.")
        return "boll", params, expl

    # ---- Donchian / breakout ----
    if "breakout" in t or "donchian" in t or "channel" in t or "highest" in t:
        n = _first_int(t, 20)
        params = {"n": n}
        expl = (f"Matched N-BAR BREAKOUT (Donchian, {n}). "
                f"Long when close exceeds the {n}-bar highest high.")
        return "donchian", params, expl

    # ---- ATR / trend ----
    if "atr" in t or "trend" in t or "momentum" in t:
        n = _first_int(t, 50)
        params = {"n": n, "atrn": 14}
        expl = (f"Matched ATR TREND FILTER (MA {n}). "
                f"Long while price holds above the {n}-bar SMA.")
        return "atr", params, expl

    # ---- SMA crossover (default for "cross"/"moving average"/generic) ----
    fast = 20
    slow = 50
    nums = [int(x) for x in _FIRST_INT.findall(t)]
    if "cross" in t or "crossover" in t:
        if len([x for x in nums if x < 100]) >= 2:
            cands = [x for x in nums if x < 100]
            fast, slow = sorted(cands[:2])
    elif any(w in t for w in ["moving average", "sma", "ma "]):
        if len([x for x in nums if x < 400]) >= 2:
            cands = [x for x in nums if x < 400]
            fast, slow = sorted(cands[:2])
    params = {"fast": fast, "slow": slow}
    expl = (f"Matched SMA CROSSOVER (fast {fast} / slow {slow}). "
            f"Long when fast SMA crosses above slow SMA.")
    return "sma", params, expl


def generate(text: str) -> str:
    """Full human-readable response for a natural-language request."""
    key, params, expl = plan_from_text(text)
    strat_cls = REGISTRY[key]
    pine = strat_cls(params=params).to_pine()
    out = []
    out.append(">> INTERPRETATION")
    out.append("   " + expl)
    out.append(">> STRATEGY KEY     : " + key)
    out.append(">> PARAMETERS       : " + str(params))
    out.append(">> PINE SCRIPT      :")
    for line in pine.splitlines():
        out.append("      " + line)
    out.append("")
    out.append("Run it with:  python run.py \"<your sentence>\"")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(generate(" ".join(sys.argv[1:])))
    else:
        print(list_strategies())
