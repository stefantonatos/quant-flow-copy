"""
opt.py - Parameter optimization (grid search) for the "QuantPad" loop.

Sweeps a small, sane grid of parameters per strategy, scores each on a
TRAIN slice, and re-checks the best candidates on a held-out TEST slice
so a winning params.combo isn't just curve-fit to the whole series.

No numpy/scipy: itertools.product over hand-picked grids is enough for
a handful of params per strategy.
"""
from __future__ import annotations

import itertools
from typing import Dict, List, Tuple

from engine import Bar, Report, run
from strategies import REGISTRY

# Small, hand-picked grids per strategy -- wide enough to matter, narrow
# enough that a full sweep finishes in a couple seconds.
PARAM_GRIDS: Dict[str, Dict[str, list]] = {
    "sma": {"fast": [10, 20, 30], "slow": [50, 100, 150]},
    "rsi": {"n": [7, 14, 21], "oversold": [20, 25, 30], "overbought": [70, 75, 80]},
    "boll": {"n": [10, 20, 30], "k": [1.5, 2.0, 2.5]},
    "atr": {"n": [20, 50, 100]},
    "donchian": {"n": [10, 20, 55]},
    "macd": {"fast": [8, 12, 16], "slow": [21, 26, 35], "signal": [9]},
    "stoch": {"n": [9, 14, 21], "oversold": [15, 20, 25], "overbought": [75, 80, 85]},
    "vwap": {"n": [10, 20, 50]},
    "supertrend": {"n": [7, 10, 14], "mult": [2.0, 3.0, 4.0]},
    "roc": {"n": [5, 10, 20]},
    "bollrsi": {"n": [15, 20, 25], "rsi_n": [10, 14], "oversold": [25, 30], "overbought": [70, 75]},
    "orb": {"range_bars": [3, 6, 12, 24]},
    "po3": {"risk_reward": [1.0, 1.5, 2.0, 3.0], "manip_end_hour": [11, 13, 16]},
    "lorentzian": {"neighbors": [5, 8, 12], "max_bars_back": [1000, 2000]},
    "sdz": {"min_rr": [2.0, 2.5, 3.0], "pivot_lookback": [3, 5, 8],
            "impulse_atr": [1.5, 2.0, 3.0]},
}


def _grid_combos(grid: Dict[str, list]):
    keys = list(grid.keys())
    for combo in itertools.product(*grid.values()):
        params = dict(zip(keys, combo))
        if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
            continue  # invalid: fast MA/period must lead the slow one
        yield params


def optimize(strategy_key: str, bars: List[Bar], capital: float = 10000.0,
             fee_bps: float = 0.0, metric: str = "sharpe", train_frac: float = 0.7,
             top_n: int = 5) -> List[Tuple[dict, Report, Report]]:
    """Grid-search a strategy's params. Returns the top `top_n` candidates
    as (params, train_report, test_report), ranked by `metric` on TRAIN.
    `metric` is any Report attribute: sharpe, total_return, profit_factor."""
    grid = PARAM_GRIDS.get(strategy_key)
    if not grid:
        raise ValueError(f"No param grid defined for '{strategy_key}'")
    strat_cls = REGISTRY[strategy_key]

    split = int(len(bars) * train_frac)
    train_bars, test_bars = bars[:split], bars[split:]

    scored = []
    for params in _grid_combos(grid):
        train_rep = run(strat_cls(bars=train_bars, params=params), capital, fee_bps)
        scored.append((params, train_rep))

    scored.sort(key=lambda x: getattr(x[1], metric), reverse=True)

    results = []
    for params, train_rep in scored[:top_n]:
        test_rep = run(strat_cls(bars=test_bars, params=params), capital, fee_bps) if test_bars else train_rep
        results.append((params, train_rep, test_rep))
    return results


def format_results(strategy_key: str, results: List[Tuple[dict, Report, Report]], metric: str) -> str:
    lines = [f"\n>> OPTIMIZATION: {REGISTRY[strategy_key].name} (ranked by train {metric})"]
    hdr = f"{'params':45s} {'train ' + metric:>12s} {'train ret':>10s} {'test ' + metric:>11s} {'test ret':>9s} {'test trades':>11s}"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for params, train_rep, test_rep in results:
        p_str = ",".join(f"{k}={v}" for k, v in params.items())
        lines.append(
            f"{p_str:45s} {getattr(train_rep, metric):12.3f} {train_rep.total_return*100:9.2f}% "
            f"{getattr(test_rep, metric):11.3f} {test_rep.total_return*100:8.2f}% {len(test_rep.trades):11d}"
        )
    lines.append("(train = first slice used to pick params; test = held-out slice -- a real edge should hold up on both)")
    return "\n".join(lines)
