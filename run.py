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
  --data sample|yahoo|stooq|binance|<csv file, directory, or glob>
                      A directory or glob of CSVs is stitched into one series
                      (deduped by timestamp), e.g. --data fixtures/data
  --symbol SYM        (for yahoo/stooq/binance)
  --range R           yahoo history range: 1mo/6mo/1y/5y/max (default 1y)
  --interval I        bar size: yahoo 1d/1wk/1mo, binance 1m/5m/15m/1h/1d... (default 1d)
  --limit N           binance: number of bars to fetch (default 365)
  --source SRC        alias for --data
  --capital N         starting capital (default 10000)
  --fee BPS           per-trade fee in basis points (default 0)
  --plot              print an ASCII equity curve
  --walkforward K     split into K contiguous out-of-sample folds
  --montecarlo N      bootstrap-resample realized trades N times
  --bracket           also run a TP1/TP2/SL bracket backtest (needs a strategy
                      that exposes start_long/start_short, e.g. lorentzian).
                      Prints both pessimistic and optimistic same-bar-tie
                      resolutions side by side -- see brackets.py.
  --sl-atr N          bracket stop distance in ATR multiples (default 1.0)
  --be-atr N          bracket breakeven+ offset in ATR multiples (default 0.15)
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
from opt import (optimize, format_results, PARAM_GRIDS,
                 is_bracket_strategy, optimize_brackets, format_bracket_results)
from brackets import run_both_policies
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


def _run_bracket(strat, bars, sl_atr, be_atr):
    """TP1/TP2/SL execution against high/low, not just closes -- the thing
    engine.run() can't do. Reports both same-bar-tie resolutions side by
    side; the gap between them is how much of any headline win rate is
    fill-order guesswork rather than real edge."""
    if not hasattr(strat, "start_long") or not hasattr(strat, "start_short"):
        print(f"\n>> BRACKET skipped: {type(strat).__name__} doesn't expose "
              f"start_long/start_short (lorentzian and sdz do).")
        return
    structural = hasattr(strat, "stops") and hasattr(strat, "targets")
    limit_entry = getattr(strat, "entries", None) is not None
    if structural:
        print(f"\n>> BRACKET (structural stops + targets from the strategy, "
              f"breakeven+ {be_atr}x ATR"
              f"{', limit entries at the marked level' if limit_entry else ''})")
    else:
        print(f"\n>> BRACKET (SL {sl_atr}x ATR, TP1 1R, TP2 2R, "
              f"breakeven+ {be_atr}x ATR)")
    # Strategies that compute their own structural levels (sdz, nowick) pass
    # them through; ones that don't (lorentzian) fall back to ATR-derived
    # stops. `entries` + allow_entry_bar_fill only apply to limit-entry
    # strategies -- see brackets._resolve on why that switch is not free.
    pess, opt = run_both_policies(bars, strat.start_long, strat.start_short,
                                  sl_atr=sl_atr, be_offset_atr=be_atr,
                                  stops=getattr(strat, "stops", None),
                                  targets=getattr(strat, "targets", None),
                                  entries=getattr(strat, "entries", None),
                                  allow_entry_bar_fill=getattr(
                                      strat, "allow_entry_bar_fill", False),
                                  partial_at_tp1=getattr(strat, "partial_at_tp1", True))
    print(pess.summary())
    print(opt.summary())


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
    bracket = False
    sl_atr = 1.0
    be_atr = 0.15
    metric = "sharpe"
    top_n = 5
    yahoo_range = "1y"
    interval = "1d"
    limit = 365
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
        if a == "--bracket":
            bracket = True; i += 1; continue
        if a == "--sl-atr":
            sl_atr = float(args[i + 1]); i += 2; continue
        if a == "--be-atr":
            be_atr = float(args[i + 1]); i += 2; continue
        if a == "--metric":
            metric = args[i + 1]; i += 2; continue
        if a == "--top":
            top_n = int(args[i + 1]); i += 2; continue
        if a == "--range":
            yahoo_range = args[i + 1]; i += 2; continue
        if a == "--interval":
            interval = args[i + 1]; i += 2; continue
        if a == "--limit":
            limit = int(args[i + 1]); i += 2; continue
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
        bars = D.from_yahoo(symbol or "SPY", interval=interval, range_=yahoo_range)
    elif data_src == "stooq":
        bars = D.from_stooq(symbol or "aapl.us")
    elif data_src == "binance":
        bars = D.from_binance(symbol or "BTCUSDT", interval=interval, limit=limit)
    elif os.path.exists(data_src) or any(ch in data_src for ch in "*?["):
        # A path, a directory, or a glob -- real exported data. Several files
        # are stitched, deduped and sorted, since a venue's download cap means
        # a year of intraday bars arrives as a dozen separate files.
        bars = D.load_path(data_src)
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
        # Bracket strategies must be scored through the bracket engine: their
        # params only move stops/targets/entries, which engine.run() cannot
        # see, so ranking them by Sharpe would rank identical numbers.
        if is_bracket_strategy(strat_key):
            print(format_bracket_results(
                strat_key, optimize_brackets(strat_key, bars, top_n=top_n)))
        else:
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
    if bracket:
        _run_bracket(strat, bars, sl_atr, be_atr)


if __name__ == "__main__":
    main(sys.argv)
