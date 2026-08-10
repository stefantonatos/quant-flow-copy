"""
run.py - CLI entry point.

Usage:
  python run.py "buy when rsi is below 30 and sell above 70"
  python run.py "sma 20 50" --data sample
  python run.py "bollinger breakout 20 2.5" --symbol AAPL --source yahoo
  python run.py list

Flags:
  --data sample|yahoo|stooq|binance
  --symbol SYM        (for yahoo/stooq/binance)
  --source SRC        alias for --data
  --capital N         starting capital (default 10000)
  --fee BPS           per-trade fee in basis points (default 0)
  --plot              print an ASCII equity curve
"""
from __future__ import annotations

import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import run
from gen import generate, plan_from_text
from strategies import REGISTRY, list_strategies
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


def main(argv):
    args = argv[1:]
    if not args or args[0] == "list":
        print(list_strategies())
        return

    # parse flags
    text_parts = []
    data_src = "sample"
    symbol = None
    capital = 10000.0
    fee = 0.0
    plot = False
    i = 0
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
        text_parts.append(a); i += 1

    text = " ".join(text_parts)

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

    strat = strat_cls(bars=bars, params=params)
    rep = run(strat, initial=capital, fee_bps=fee)
    print(rep.summary())
    if plot:
        print("\nEquity curve:")
        print(_ascii_curve(rep.equity))


if __name__ == "__main__":
    main(sys.argv)
