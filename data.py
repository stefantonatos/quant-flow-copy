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
import datetime
import re
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


# ---------------------------------------------------------------------------
# GETTING REAL DATA IN
# ---------------------------------------------------------------------------
# This container has no market-data egress -- Yahoo, Stooq, Binance and
# Dukascopy all get a 403 policy denial at the gateway, verified, not assumed.
# So real data arrives as a CSV committed to the repo, and load_csv below is
# deliberately permissive about what that CSV looks like.
#
# Two good free sources for forex, both producing CSVs this loader accepts:
#
#   Dukascopy Historical Data Export
#     https://www.dukascopy.com/swiss/english/marketwatch/historical/
#     Free, tick-level back 10+ years, aggregates to any timeframe. The time
#     column is headed "Gmt time" and looks like "01.07.2025 09:05:00.000".
#     Already UTC, which is what every session strategy here assumes.
#
#   TradingView chart export (paid plans)
#     Exports the loaded chart. Set the chart to UTC before exporting, or the
#     session boundaries in asiasweep/po3/orb will be silently offset.
#
# Whatever the source: keep it UTC, keep the OHLC columns, and let the
# timestamp parser below deal with the format.
# ---------------------------------------------------------------------------

_TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",      # ISO 8601, the TradingView export shape
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",      # Dukascopy "Gmt time" column
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%Y.%m.%d %H:%M:%S",      # MetaTrader export
    "%Y.%m.%d %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S",
)


def _to_epoch(x) -> float:
    """Parse a timestamp cell into UTC epoch seconds.

    Real exports do not hand you epoch integers. Dukascopy writes
    "01.07.2025 00:00:00.000" under a "Gmt time" header, TradingView writes
    ISO 8601, MetaTrader writes "2025.07.01 00:00". Before this existed,
    every one of those fell through float() and became NaN -- and a NaN
    timestamp is not a loud failure, it is a silent one: the session-based
    strategies (asiasweep, po3, orb) would just quietly produce no trades,
    which looks exactly like "no setups found".

    Naive strings are treated as UTC. That is correct for Dukascopy (the
    column is literally GMT) and for TradingView exports of a UTC chart; if
    your export is in local time, convert it before loading or every session
    boundary in this repo will be wrong.
    """
    if x is None:
        return float("nan")
    s = str(x).strip()
    if not s:
        return float("nan")

    # Plain number: epoch seconds, or milliseconds if it is far too large.
    try:
        v = float(s)
        if v > 1e14:      # microseconds
            return v / 1e6
        if v > 1e11:      # milliseconds
            return v / 1e3
        return v
    except ValueError:
        pass

    t = s.replace("Z", "").replace("z", "")
    t = re.sub(r"([+-]\d{2}:?\d{2})$", "", t).strip()   # drop any offset
    t = re.sub(r"\.\d+$", "", t)                        # drop sub-seconds

    for fmt in _TS_FORMATS:
        try:
            dt = datetime.datetime.strptime(t, fmt)
            return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            continue
    return float("nan")


def load_csv(path: str) -> List[Bar]:
    bars = []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            cols = row.keys()
            def g(*names, parse=_to_float):
                for n in names:
                    if n in row:
                        return parse(row[n])
                    for k in cols:                 # case/space tolerant
                        if k and k.strip().lower() == n.strip().lower():
                            return parse(row[k])
                return float("nan")
            ts = g("t", "time", "date", "timestamp", "datetime",
                   "gmt time", "Gmt time", "Date", "Time", "Datetime",
                   "Local time", "local time", parse=_to_epoch)
            if ts != ts and cols:
                # No recognised name matched. Dukascopy's export names the
                # time column after the TIMEZONE -- "Etc/UTC", "Europe/Zurich"
                # -- so there is no fixed name to look for and the list above
                # can never be made complete. The first column is the time
                # column in every OHLC export any of these venues produce, so
                # fall back to position when the name is unrecognised.
                ts = _to_epoch(row[next(iter(cols))])
            bars.append(Bar(
                t=ts,
                o=g("o", "open"),
                h=g("h", "high"),
                l=g("l", "low"),
                c=g("c", "close"),
                v=g("v", "volume", "vol"),
            ))
    # Fail loudly. An unparsed timestamp column is not a loud error on its
    # own -- the session-based strategies just quietly find no setups, which
    # is indistinguishable from "this market had no setups". Say so instead.
    bad = sum(1 for b in bars if b.t != b.t)
    if bars and bad:
        print(f"   WARNING: {bad}/{len(bars)} rows have an unparsable timestamp. "
              f"Session strategies (asiasweep, po3, orb) need real UTC times and "
              f"will silently produce nothing. Check the time column's format.")
    return bars


def load_csv_many(paths: List[str]) -> List[Bar]:
    """Load and stitch several CSV exports into one continuous series.

    Dukascopy's web export caps a 1-hour download at roughly one month, so a
    year of data arrives as ~12 separate files. This concatenates them,
    DEDUPES BY TIMESTAMP and sorts chronologically, so overlapping or
    out-of-order downloads are safe -- the ranges do not have to be picked
    carefully, and re-sending a file you already sent changes nothing.

    Deduping matters more than it looks: a duplicated bar is not a rounding
    error, it is a second chance for a strategy to trade the same moment,
    which quietly inflates the trade count and every statistic derived from
    it.
    """
    seen = {}
    for p in paths:
        for b in load_csv(p):
            if b.t == b.t:            # drop unparsable rows, already warned
                seen[b.t] = b
    return [seen[t] for t in sorted(seen)]


def resample(bars: List[Bar], seconds: int) -> List[Bar]:
    """Aggregate bars up to a coarser timeframe (e.g. 1-minute -> 1-hour).

    Buckets are floor(t / seconds), so a bucket that no bar falls into simply
    does not exist. That is what makes this safe across weekends and session
    breaks: it never invents a flat bar to bridge a gap the market was closed
    for, which would put a fake price into every indicator that reads it.

    OHLC is aggregated the only way that is meaningful -- open from the FIRST
    bar in the bucket, close from the LAST, high/low as the extremes, volume
    summed. Bars must already be sorted; load_csv_many() guarantees that.
    """
    if seconds <= 0:
        raise ValueError("seconds must be > 0")
    out: List[Bar] = []
    cur_key = None
    for b in bars:
        if b.t != b.t:
            continue
        key = int(b.t // seconds)
        if key != cur_key:
            out.append(Bar(t=key * seconds, o=b.o, h=b.h, l=b.l, c=b.c, v=b.v))
            cur_key = key
        else:
            agg = out[-1]
            agg.h = max(agg.h, b.h)
            agg.l = min(agg.l, b.l)
            agg.c = b.c
            agg.v += b.v
    return out


def load_path(spec: str) -> List[Bar]:
    """Load a CSV file, a directory of CSVs, or a glob pattern."""
    import glob as _glob
    if os.path.isdir(spec):
        paths = sorted(_glob.glob(os.path.join(spec, "*.csv")))
    elif any(ch in spec for ch in "*?["):
        paths = sorted(_glob.glob(spec))
    else:
        paths = [spec]
    if not paths:
        raise FileNotFoundError(f"no CSV files matched: {spec}")
    return load_csv_many(paths) if len(paths) > 1 else load_csv(paths[0])


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
