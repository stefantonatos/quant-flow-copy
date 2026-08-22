"""
dukascopy_feed.py - Read Dukascopy's REAL historical archive.

WHY THIS EXISTS (read before "simplifying" it)
The first attempt used the `dukascopy-python` package, which talks to
freeserv.dukascopy.com -- the endpoint behind their *chart widget*. That
endpoint serves data thinned for display: asking it for 1-hour NAS100 bars
over 2020-2026 returned 7,304 bars covering only 2022-06 onward, roughly 7
bars per trading day where the truth is about 23. It never errors. It just
quietly hands back a sparser, shorter series than you asked for, and a VWAP
built from a third of a session's bars is simply a wrong number.

This module reads `www.dukascopy.com/datafeed/...` instead -- the archive
their own desktop platform downloads. Complete, unthinned, back to the
instrument's inception.

FORMAT NOTES, all load-bearing:
  * The MONTH IN THE URL IS ZERO-INDEXED. January is 00. This is the single
    most common way to fetch the wrong month and not notice.
  * Files are raw LZMA (not .xz), decompressed with FORMAT_AUTO.
  * A missing file means "market closed", not an error -- weekends and
    holidays 404 by design and must be skipped silently.
  * Candle records are 24 bytes big-endian: a uint32 second-offset from the
    file's period start, four int32 prices, and a float32 volume. Prices are
    integers scaled by 10^decimals (3 for index CFDs, 5 for most FX).
  * The ORDER of those four prices is not something to assume. Sources
    disagree on whether it is (open, close, low, high) or (open, high, low,
    close), so this module tries both and keeps whichever actually satisfies
    high >= max(open, close) and low <= min(open, close). Guessing wrong
    silently swaps highs and closes, which would corrupt every ATR and every
    intrabar test downstream without ever raising.

Stdlib only, matching the rest of this repo: urllib + lzma + struct.
"""
from __future__ import annotations

import datetime
import lzma
import struct
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

from engine import Bar

BASE = "https://www.dukascopy.com/datafeed"

# Instrument -> price scaling. Dukascopy stores prices as integers; divide by
# 10^decimals. Index CFDs quote 3 decimals (21070.242), most FX pairs 5.
DECIMALS = {
    "USATECHIDXUSD": 3, "USA500IDXUSD": 3, "USA30IDXUSD": 3,
    "USSC2000IDXUSD": 3, "DEUIDXEUR": 3, "GBRIDXGBP": 3, "JPNIDXJPY": 3,
    "XAUUSD": 3,
}
DEFAULT_DECIMALS = 5           # FX majors/crosses

#   uint32 second-offset | 4x int32 price | float32 volume  == 24 bytes
CANDLE_STRUCT = struct.Struct(">Iiiiif")
CANDLE_SIZE = CANDLE_STRUCT.size

# The two orderings seen in the wild for the four price fields.
LAYOUTS = {
    "ocLh": ("open", "close", "low", "high"),
    "ohLc": ("open", "high", "low", "close"),
}


def _url_hour_candles(symbol: str, year: int, month0: int) -> str:
    """One file per MONTH holding that month's hourly candles."""
    return f"{BASE}/{symbol}/{year}/{month0:02d}/BID_candles_hour_1.bi5"


def _url_min_candles(symbol: str, year: int, month0: int, day: int) -> str:
    """One file per DAY holding that day's 1-minute candles."""
    return f"{BASE}/{symbol}/{year}/{month0:02d}/{day:02d}/BID_candles_min_1.bi5"


def _get(url: str, timeout: int = 30) -> Optional[bytes]:
    """Return raw bytes, or None when the file does not exist.

    A 404 is the normal way this archive says "no data for that period"
    (weekend, holiday, before the instrument existed). Treating it as an
    error would abort a multi-year fetch on the first Saturday.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def decompress(raw: bytes) -> bytes:
    """LZMA-decompress one .bi5 payload. Empty input -> empty output."""
    if not raw:
        return b""
    d = lzma.LZMADecompressor(lzma.FORMAT_AUTO, None, None)
    return d.decompress(raw)


def _score_layout(records: List[tuple], order: Tuple[str, ...]) -> int:
    """How many records VIOLATE the OHLC invariants under this field order.
    Lower is better; the correct layout should score 0."""
    idx = {name: i for i, name in enumerate(order)}
    bad = 0
    for rec in records:
        o = rec[1 + idx["open"]]
        h = rec[1 + idx["high"]]
        l = rec[1 + idx["low"]]
        c = rec[1 + idx["close"]]
        if h < max(o, c) or l > min(o, c) or h < l:
            bad += 1
    return bad


def parse_candles(blob: bytes, period_start: datetime.datetime,
                  scale: float, layout: Optional[str] = None
                  ) -> Tuple[List[Bar], str]:
    """Decode a decompressed candle payload into Bars.

    Returns (bars, layout_used). When `layout` is None the field order is
    detected by scoring both candidates against the OHLC invariants -- see
    the module docstring for why this is not assumed.
    """
    n = len(blob) // CANDLE_SIZE
    if n == 0:
        return [], layout or ""
    records = [CANDLE_STRUCT.unpack_from(blob, i * CANDLE_SIZE) for i in range(n)]

    if layout is None:
        scored = sorted(LAYOUTS, key=lambda k: _score_layout(records, LAYOUTS[k]))
        layout = scored[0]
        if _score_layout(records, LAYOUTS[layout]) > 0.02 * n:
            raise ValueError(
                "Neither candle field order satisfies the OHLC invariants. "
                "The record format has changed -- do not trust this data.")

    order = LAYOUTS[layout]
    idx = {name: i for i, name in enumerate(order)}
    bars: List[Bar] = []
    for rec in records:
        t = period_start + datetime.timedelta(seconds=rec[0])
        bars.append(Bar(
            t=t.replace(tzinfo=datetime.timezone.utc).timestamp(),
            o=rec[1 + idx["open"]] / scale,
            h=rec[1 + idx["high"]] / scale,
            l=rec[1 + idx["low"]] / scale,
            c=rec[1 + idx["close"]] / scale,
            v=float(rec[5]),
        ))
    return bars, layout


def is_padding(b: Bar) -> bool:
    """True for a filler bar the archive emits for a CLOSED hour.

    The hourly files are dense over the calendar -- 744 records for a 31-day
    month, i.e. every hour of every day including weekends. Hours the market
    was shut are padded with a zero-volume, zero-range record carrying the
    last price. Dukascopy's own web export omits them, which is how they were
    caught: a hand-downloaded January 2025 had 495 bars where the archive had
    744.

    They must be dropped, and not merely for tidiness. Session VWAP is a
    cumulative average over every bar in the session, so ~250 fake flat bars
    a month would drag it toward a stale price; ATR would be diluted by
    zero-range bars; and the strategy would be handed entry opportunities at
    hours the market was closed and no fill was possible.
    """
    return b.v <= 0 and b.o == b.h == b.l == b.c


def fetch_hourly(symbol: str, start: datetime.datetime, end: datetime.datetime,
                 progress: bool = True, drop_padding: bool = True) -> List[Bar]:
    """Hourly bars for [start, end), read month by month from the archive."""
    scale = 10 ** DECIMALS.get(symbol.upper(), DEFAULT_DECIMALS)
    out: List[Bar] = []
    layout: Optional[str] = None
    cur = datetime.datetime(start.year, start.month, 1)
    while cur < end:
        raw = _get(_url_hour_candles(symbol, cur.year, cur.month - 1))
        got = 0
        dropped = 0
        if raw:
            bars, layout = parse_candles(decompress(raw), cur, scale, layout)
            keep = [b for b in bars
                    if start.timestamp() <= b.t < end.timestamp()]
            if drop_padding:
                before = len(keep)
                keep = [b for b in keep if not is_padding(b)]
                dropped = before - len(keep)
            out.extend(keep)
            got = len(keep)
        if progress:
            note = "" if raw else "   (no file)"
            if dropped:
                note = f"   ({dropped:,} closed-hour padding dropped)"
            print(f"   {cur:%Y-%m}: {got:>5,} bars{note}")
        cur = (cur.replace(day=28) + datetime.timedelta(days=8)).replace(day=1)
    out.sort(key=lambda b: b.t)
    # Dedupe: a repeated timestamp is a second chance to trade one moment.
    seen = {}
    for b in out:
        seen[b.t] = b
    return [seen[t] for t in sorted(seen)]
