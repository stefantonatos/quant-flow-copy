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
