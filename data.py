"""
data.py - Market data adapters (free, no API key required on your machine).

Your sandbox has no internet, so the live calls below are written to work
on YOUR Windows machine, where network is available. For a guaranteed
offline demo, `sample_csv()` returns a bundled synthetic-but-realistic
series so the engine can always be proven locally.

Recommended free sources (no key):
  - Yahoo Finance  (stocks/ETF/crypto/fx)  - yfinance or direct CSV
  - Stooq          (stocks/idx/fx)         - direct CSV, very reliable
  - Binance        (crypto)                - public REST, no key

This module uses only stdlib so it runs anywhere.
"""
from __future__ import annotations

import csv
import math
import os
import urllib.request
from typing import List

from engine import Bar

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_CSV = os.path.join(HERE, "sample_data.csv")


def _to_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return float("nan")


def sample_csv(path: str = SAMPLE_CSV) -> str:
    """Generate a realistic 600-bar random-walk-with-trend series and
    write it to path. Returns the path."""
    import random
    random.seed(42)
    rows = [("t", "o", "h", "l", "c", "v")]
    px = 100.0
    t = 1_600_000_000
    for i in range(600):
        drift = 0.02 * math.sin(i / 40.0)          # slow regime
        shock = random.gauss(0, 0.6)
        o = px
        c = max(1.0, o + drift + shock)
        h = max(o, c) + abs(random.gauss(0, 0.3))
        l = min(o, c) - abs(random.gauss(0, 0.3))
        v = random.uniform(500, 5000)
        rows.append((t, f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{c:.2f}", f"{v:.0f}"))
        px = c
        t += 86400
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerows(rows)
    return path


def load_csv(path: str) -> List[Bar]:
    bars = []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            cols = row.keys()
            def g(*names):
                for n in names:
                    if n in row:
                        return _to_float(row[n])
                return float("nan")
            bars.append(Bar(
                t=g("t", "time", "date", "timestamp"),
                o=g("o", "open"),
                h=g("h", "high"),
                l=g("l", "low"),
                c=g("c", "close"),
                v=g("v", "volume", "vol"),
            ))
    return bars


def from_yahoo(symbol: str, interval: str = "1d", range_: str = "1y") -> List[Bar]:
    """Fetch OHLCV from Yahoo Finance (no key). Requires network.
    interval: 1d/1wk/1mo; range: 1d/1mo/6mo/1y/5y/max"""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval={interval}&range={range_}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = __import__("json").loads(resp.read())
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    bars = []
    for i, (t, o, h, l, c, v) in enumerate(
        zip(ts, q["open"], q["high"], q["low"], q["close"], q["volume"])):
        if None in (o, h, l, c):
            continue
        bars.append(Bar(t=float(t), o=float(o), h=float(h),
                        l=float(l), c=float(c), v=float(v or 0)))
    return bars


def from_stooq(symbol: str) -> List[Bar]:
    """Fetch daily OHLCV from stooq.com (no key). symbol e.g. 'aapl.us'."""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    txt = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
    bars = []
    lines = txt.strip().splitlines()
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        from datetime import datetime
        try:
            t = datetime.strptime(parts[0], "%Y-%m-%d").timestamp()
        except Exception:
            t = 0.0
        bars.append(Bar(t=t, o=_to_float(parts[1]), h=_to_float(parts[2]),
                        l=_to_float(parts[3]), c=_to_float(parts[4]),
                        v=_to_float(parts[5])))
    return bars


def from_binance(symbol: str = "BTCUSDT", interval: str = "1d",
                 limit: int = 365) -> List[Bar]:
    """Fetch klines from Binance (no key)."""
    url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
           f"&interval={interval}&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    txt = urllib.request.urlopen(req, timeout=20).read()
    raw = __import__("json").loads(txt)
    bars = []
    for k in raw:
        bars.append(Bar(t=float(k[0]) / 1000.0, o=_to_float(k[1]),
                        h=_to_float(k[2]), l=_to_float(k[3]),
                        c=_to_float(k[4]), v=_to_float(k[5])))
    return bars
