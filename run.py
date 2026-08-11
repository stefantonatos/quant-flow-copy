"""
run.py - CLI entry point.

Usage:
  python run.py "buy when rsi is below 30 and sell above 70"
  python run.py "sma 20 50" --data sample
  python run.py "bollinger breakout 20 2.5" --symbol AAPL --source yahoo
  python run.py list
  python run.py compare --data sample
  python run.py "macd" --data sample --walkforward 4 --montecarlo 1000

Flags:
  --data sample|yahoo|stooq|binance
  --symbol SYM        (for yahoo/stooq/binance)
  --source SRC        alias for --data
  --capital N         starting capital (default 10000)
  --fee BPS           per-trade fee in basis points (default 0)
  --plot              print an ASCII equity curve
  --walkforward K     split into K contiguous out-of-sample folds
  --montecarlo N      bootstrap-resample realized trades N times
  --metric M          optimize: rank by sharpe|total_return|profit_factor (default sharpe)
  --top N             optimize: how many top candidates to show (default 5)

Modes:
  list      show all built-in strategies
  compare   screener: run every strategy on the same data, rank by Sharpe
  optimize  grid-search a strategy's params, validated on a held-out slice
            e.g. python run.py optimize bollrsi --data sample --metric sharpe
"""
from __future__ import annotations

import sys
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import run
from gen import generate, plan_from_text
from strategies import REGISTRY, list_strategies
from opt import optimize, format_results, PARAM_GRIDS
import data as D


def _ascii_curve(equity, width=60, height=10):
    lo, hi = min(equity), max(equity)
    if hi == lo:
        return "(flat)"
    rows = []
    for row in range(height, 0, -1):
        thr = lo + (hi - lo) * (row - 0.5) / height
        line = ""
        step = max(1, len(equity) // width)
        for i in range(0, len(equity), step):
            line += "#" if equity[i] >= thr else " "
        rows.append(line)
    return "\n".join(rows)


def _run_compare(bars, capital, fee):
    """Screener-style comparison: every built-in strategy, default params,
    same data, ranked by Sharpe."""
    print("\n>> STRATEGY SCREENER (defaults, full period)")
    rows = []
    for key, cls in REGISTRY.items():
        strat = cls(bars=bars, params={})
        rep = run(strat, initial=capital, fee_bps=fee)
        grade, _ = rep.verdict()
        rows.append((key, cls.name, rep.total_return, rep.sharpe, rep.max_dd, len(rep.trades), grade))
    rows.sort(key=lambda r: r[3], reverse=True)
    hdr = f"{'key':10s} {'strategy':22s} {'return':>8s} {'sharpe':>7s} {'maxdd':>7s} {'trades':>7s} grade"
    print(hdr)
    print("-" * len(hdr))
    for key, name, ret, sharpe, dd, ntrades, grade in rows:
        print(f"{key:10s} {name:22s} {ret*100:7.2f}% {sharpe:7.2f} {dd*100:6.2f}% {ntrades:7d} {grade}")


def _run_walkforward(strat_cls, params, bars, capital, fee, folds):
    """Fixed-rule out-of-sample check: split the series into contiguous
    folds and see if the same strategy+params holds up in each one."""
    n = len(bars)
    if folds < 2 or n < folds * 30:
        print(f"\n>> WALK-FORWARD skipped: need >= {folds * 30} bars for {folds} folds, have {n}.")
        return
    print(f"\n>> WALK-FORWARD ({folds} out-of-sample folds, fixed params)")
    size = n // folds
    rets, sharpes, dds = [], [], []
    for f in range(folds):
        lo = f * size
        hi = n if f == folds - 1 else (f + 1) * size
        fold_bars = bars[lo:hi]
        strat = strat_cls(bars=fold_bars, params=params)
        rep = run(strat, initial=capital, fee_bps=fee)
        rets.append(rep.total_return); sharpes.append(rep.sharpe); dds.append(rep.max_dd)
        print(f"   fold {f + 1}/{folds}  bars={len(fold_bars):4d}  "
              f"return={rep.total_return * 100:7.2f}%  sharpe={rep.sharpe:6.2f}  maxdd={rep.max_dd * 100:6.2f}%")
    pos_folds = sum(1 for r in rets if r > 0)
    print(f"   -- avg return {sum(rets) / len(rets) * 100:.2f}%  "
          f"avg sharpe {sum(sharpes) / len(sharpes):.2f}  worst dd {max(dds) * 100:.2f}%  "
          f"profitable folds {pos_folds}/{folds}")


def _run_montecarlo(rep, capital, sims):
    """Bootstrap-resample the realized trade sequence to see how much of
    the result is luck of the draw vs. sequence risk."""
    pnl_pcts = [t.pnl_pct for t in rep.trades]
    if len(pnl_pcts) < 5:
        print(f"\n>> MONTE CARLO skipped: only {len(pnl_pcts)} trades, need >= 5.")
        return
    print(f"\n>> MONTE CARLO ({sims} resamples of {len(pnl_pcts)} realized trades)")
    finals, maxdds = [], []
    for _ in range(sims):
        eq = capital
        peak = eq
        dd = 0.0
        for _ in pnl_pcts:
            eq *= 1 + random.choice(pnl_pcts)
            peak = max(peak, eq)
            if peak > 0:
                dd = max(dd, (peak - eq) / peak)
        finals.append(eq)
        maxdds.append(dd)
    finals.sort(); maxdds.sort()

    def pct(vals, p):
        return vals[min(len(vals) - 1, int(p * len(vals)))]

    print(f"   final equity   P5={pct(finals, 0.05):,.2f}  P50={pct(finals, 0.50):,.2f}  P95={pct(finals, 0.95):,.2f}")
    print(f"   max drawdown   P50={pct(maxdds, 0.50) * 100:.2f}%  P95={pct(maxdds, 0.95) * 100:.2f}%")


def main(argv):
    args = argv[1:]
    if not args or args[0] == "list":
        print(list_strategies())
        return

    compare = args[0] == "compare"
    optimize_mode = args[0] == "optimize"

    # parse flags
    text_parts = []
    data_src = "sample"
    symbol = None
    capital = 10000.0
    fee = 0.0
    plot = False
    walkforward = 0
    montecarlo = 0
    metric = "sharpe"
    top_n = 5
    i = 1 if (compare or optimize_mode) else 0
    while i < len(args):
        a = args[i]
        if a == "--data" or a == "--source":
            data_src = args[i + 1].lower(); i += 2; continue
        if a == "--symbol":
            symbol = args[i + 1]; i += 2; continue
        if a == "--capital":
            capital = float(args[i + 1]); i += 2; continue
        if a == "--fee":
            fee = float(args[i + 1]); i += 2; continue
        if a == "--plot":
            plot = True; i += 1; continue
        if a == "--walkforward":
            walkforward = int(args[i + 1]); i += 2; continue
        if a == "--montecarlo":
            montecarlo = int(args[i + 1]); i += 2; continue
        if a == "--metric":
            metric = args[i + 1]; i += 2; continue
        if a == "--top":
            top_n = int(args[i + 1]); i += 2; continue
        text_parts.append(a); i += 1

    text = " ".join(text_parts)

    if optimize_mode:
        strat_key = text_parts[0] if text_parts else None
        if strat_key not in PARAM_GRIDS:
            print(f"Usage: python run.py optimize <key> [--data ...] [--metric sharpe|total_return|profit_factor] [--top N]")
            print(f"Strategies with a param grid: {', '.join(PARAM_GRIDS)}")
            return

    if not compare and not optimize_mode:
        print(generate(text))
        print()
        key, params, _ = plan_from_text(text)
        strat_cls = REGISTRY[key]

    # ---- acquire data ----
    print(f">> LOADING DATA (source={data_src})")
    if data_src in ("sample", "offline"):
        path = D.sample_csv()
        bars = D.load_csv(path)
    elif data_src in ("yahoo", "yf"):
        bars = D.from_yahoo(symbol or "SPY")
    elif data_src == "stooq":
        bars = D.from_stooq(symbol or "aapl.us")
    elif data_src == "binance":
        bars = D.from_binance(symbol or "BTCUSDT")
    else:
        print(f"Unknown source '{data_src}', falling back to sample.")
        bars = D.load_csv(D.sample_csv())

    print(f"   bars loaded: {len(bars)}")
    if not bars:
        print("   No data. Aborting.")
        return

    if compare:
        _run_compare(bars, capital, fee)
        return

    if optimize_mode:
        results = optimize(strat_key, bars, capital, fee, metric=metric, top_n=top_n)
        print(format_results(strat_key, results, metric))
        return

    strat = strat_cls(bars=bars, params=params)
    rep = run(strat, initial=capital, fee_bps=fee)
    print(rep.summary())
    if plot:
        print("\nEquity curve:")
        print(_ascii_curve(rep.equity))
    if walkforward:
        _run_walkforward(strat_cls, params, bars, capital, fee, walkforward)
    if montecarlo:
        _run_montecarlo(rep, capital, montecarlo)


if __name__ == "__main__":
    main(sys.argv)
