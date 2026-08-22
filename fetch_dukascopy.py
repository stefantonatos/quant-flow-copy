"""
fetch_dukascopy.py - Pull real Dukascopy history into a CSV this repo reads.

RUN THIS ON YOUR OWN MACHINE, NOT IN THE CLOUD SANDBOX.
The sandbox cannot reach any market-data host; www.dukascopy.com returns
"403 Forbidden" at the proxy there. Your laptop just works.

WHICH ENDPOINT, AND WHY IT MATTERS
This reads www.dukascopy.com/datafeed -- the archive Dukascopy's own desktop
platform downloads. An earlier version of this script used the pip package
`dukascopy-python`, which talks to their CHART WIDGET api instead. That one
returns data thinned for display and does not say so: asking it for 1-hour
NAS100 over 2020-2026 gave 7,304 bars starting only in 2022, about 7 bars per
trading day where the truth is ~23. No error, no warning. A VWAP built from a
third of each session's bars is simply wrong, so that whole route was dropped.

SETUP: nothing to install. Stdlib only.

USAGE:
    python fetch_dukascopy.py                        # Nasdaq 100, 1h, 2020->today
    python fetch_dukascopy.py --start 2015-01-01
    python fetch_dukascopy.py --symbol USA500IDXUSD  # S&P 500
    python fetch_dukascopy.py --symbol EURNZD --start 2024-01-01

It self-checks: whenever the range overlaps a reference CSV in fixtures/data,
the two are compared bar for bar and any disagreement is reported.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import os
import sys

import data as D
import dukascopy_feed as F

# Dukascopy's datafeed symbols. These are NOT the names on the website's
# dropdown -- USATECHIDXUSD is what the archive calls the Nasdaq 100.
SYMBOLS = {
    "USATECHIDXUSD": "Nasdaq 100 index CFD",
    "USA500IDXUSD": "S&P 500 index CFD",
    "USA30IDXUSD": "Dow 30 index CFD",
    "USSC2000IDXUSD": "Russell 2000 index CFD",
    "DEUIDXEUR": "DAX index CFD",
    "XAUUSD": "Gold",
    "EURUSD": "EUR/USD",
    "EURNZD": "EUR/NZD",
    "GBPUSD": "GBP/USD",
}


def verify(bars, ref_path: str) -> None:
    """Compare freshly fetched bars against a known-good export.

    This is the step that turns "the code ran" into "the numbers are right".
    The reference is a CSV downloaded by hand from Dukascopy's web UI, so
    agreement means the URL, the zero-indexed month, the LZMA framing, the
    record layout and the price scale are ALL correct at once. Any one of
    them being wrong shows up here as a mismatch rather than as a plausible
    but false backtest months later.
    """
    ref = {b.t: b for b in D.load_csv(ref_path) if b.t == b.t}
    if not ref:
        return
    ours = {b.t: b for b in bars}
    common = sorted(set(ref) & set(ours))
    if not common:
        return

    worst = 0.0
    worst_at = None
    for t in common:
        a, b = ref[t], ours[t]
        for x, y in ((a.o, b.o), (a.h, b.h), (a.l, b.l), (a.c, b.c)):
            if x == 0:
                continue
            d = abs(x - y) / abs(x)
            if d > worst:
                worst, worst_at = d, t

    print()
    print(f">> VERIFY against {os.path.basename(ref_path)}")
    print(f"   overlapping bars: {len(common):,} of {len(ref):,} in the reference")
    missing = len(set(ref) - set(ours))
    if missing:
        print(f"   !! {missing:,} reference bars are MISSING from the fetch")
    if worst_at is not None:
        when = datetime.datetime.utcfromtimestamp(worst_at)
        print(f"   largest price disagreement: {worst*100:.4f}%  at {when:%Y-%m-%d %H:%M}")
    if worst < 1e-6 and not missing:
        print("   PASS -- identical to the hand-downloaded reference.")
    elif worst < 1e-3:
        print("   PASS -- differences are rounding-level.")
    else:
        print("   !! FAIL -- these are not the same bars. Do NOT backtest this "
              "file until the cause is understood.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="USATECHIDXUSD",
                    help="datafeed symbol (default USATECHIDXUSD = Nasdaq 100)")
    ap.add_argument("--start", default="2020-01-01", help="YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--out", default=None, help="output CSV path")
    ap.add_argument("--list", action="store_true", help="list known symbols and exit")
    args = ap.parse_args()

    if args.list:
        for k, v in SYMBOLS.items():
            print(f"  {k:18} {v}")
        return 0

    sym = args.symbol.upper()
    if sym not in SYMBOLS:
        print(f"'{sym}' is not in the known list (it may still work).")
        print("Known symbols:")
        for k, v in SYMBOLS.items():
            print(f"  {k:18} {v}")
        print()

    start = datetime.datetime.strptime(args.start, "%Y-%m-%d")
    end = (datetime.datetime.strptime(args.end, "%Y-%m-%d") if args.end
           else datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))

    print(f">> {sym} ({SYMBOLS.get(sym, 'unknown instrument')}) @ 1h")
    print(f">> {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    print(">> reading the datafeed archive, one file per month")

    try:
        bars = F.fetch_hourly(sym, start, end)
    except Exception as exc:
        print(f"FETCH FAILED: {type(exc).__name__}: {exc}")
        print("If this mentions 403 or a proxy, you are on a machine without "
              "access to dukascopy.com -- run it where you have normal internet.")
        return 1

    if not bars:
        print("No bars returned. Check the symbol (--list) and the date range.")
        return 1

    out = args.out or os.path.join(
        "fixtures", "data", f"{sym}_1h_{start:%Y%m%d}_{end:%Y%m%d}.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        for b in bars:
            w.writerow([int(b.t), b.o, b.h, b.l, b.c, b.v])

    first = datetime.datetime.utcfromtimestamp(bars[0].t)
    last = datetime.datetime.utcfromtimestamp(bars[-1].t)
    print()
    print(f">> wrote {len(bars):,} bars to {out}")
    print(f">> covering {first:%Y-%m-%d %H:%M} -> {last:%Y-%m-%d %H:%M} UTC")

    span_days = max((last - first).days, 1)
    per_day = len(bars) / (span_days * 5 / 7)
    print(f">> density: {per_day:.1f} bars per trading day "
          f"(a 23-hour index CFD should be near 23)")
    if per_day < 12:
        print("   !! That is too sparse for hourly data. Something thinned it.")

    # Auto-verify against any reference export sitting in fixtures/data.
    for ref in sorted(glob.glob(os.path.join("fixtures", "data", "USATECH*.csv"))):
        if os.path.abspath(ref) != os.path.abspath(out):
            verify(bars, ref)

    print()
    print("Now run:")
    print(f'   python run.py "vwap fade" --data {out} --fee 1')
    return 0


if __name__ == "__main__":
    sys.exit(main())
