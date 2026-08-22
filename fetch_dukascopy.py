"""
fetch_dukascopy.py - Pull Dukascopy history straight into a CSV this repo reads.

RUN THIS ON YOUR OWN MACHINE, NOT IN THE CLOUD SANDBOX.
The sandbox this project is developed in cannot reach any market-data host --
freeserv.dukascopy.com returns "403 Forbidden" at the proxy, and so does every
other data vendor. Your laptop has normal internet, so it just works there.
Confirmed failure mode in the sandbox, for whoever tries again:

    ProxyError: HTTPSConnectionPool(host='freeserv.dukascopy.com', port=443)
    ... Tunnel connection failed: 403 Forbidden

WHY THIS BEATS THE WEB EXPORT
Dukascopy's browser export caps a 1-hour download at roughly one month, so a
few years of data is dozens of manual downloads. This asks for the whole range
in one go, month by month under the hood, and writes a single CSV.

SETUP (once):
    pip install dukascopy-python

USAGE:
    python fetch_dukascopy.py                       # Nasdaq 100, 1h, 2020->today
    python fetch_dukascopy.py --start 2015-01-01
    python fetch_dukascopy.py --interval 15m --start 2024-01-01
    python fetch_dukascopy.py --instrument EUR_USD  --interval 15m

Then back in this repo:
    python run.py "vwap fade" --data fixtures/data/<the-file-it-wrote>.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys

# Friendly names -> the library's instrument constants. Add more by running
#   python -c "import dukascopy_python.instruments as I; print(dir(I))"
INSTRUMENTS = {
    # Every name here was checked against the installed library, not guessed --
    # three plausible-looking guesses (US_SPX_500, US_30, VCCY_XAU) turned out
    # not to exist. If one stops resolving, --instrument prints the live list.
    "NAS100": "INSTRUMENT_US_TECH_US_USD",       # Nasdaq 100 CFD -- the default
    "SPX500": "INSTRUMENT_US_IDXX_US_USD",       # S&P 500 CFD
    "US2000": "INSTRUMENT_IDX_AMERICA_USSC2000_IDX_USD",   # Russell 2000
    "NQ_FUT": "INSTRUMENT_IDX_AMERICA_E_NQ_100",           # E-mini Nasdaq future
    "ES_FUT": "INSTRUMENT_IDX_AMERICA_E_SANDP_500",        # E-mini S&P future
    "YM_FUT": "INSTRUMENT_IDX_AMERICA_E_D_J_IND",          # E-mini Dow future
    "XAUUSD": "INSTRUMENT_FX_METALS_XAU_USD",
    "EUR_USD": "INSTRUMENT_FX_MAJORS_EUR_USD",
    "EUR_NZD": "INSTRUMENT_FX_CROSSES_EUR_NZD",
}

INTERVALS = {
    "1m": "INTERVAL_MIN_1", "5m": "INTERVAL_MIN_5", "15m": "INTERVAL_MIN_15",
    "30m": "INTERVAL_MIN_30", "1h": "INTERVAL_HOUR_1", "4h": "INTERVAL_HOUR_4",
    "1d": "INTERVAL_DAY_1",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instrument", default="NAS100",
                    help=f"one of {', '.join(INSTRUMENTS)} (default NAS100)")
    ap.add_argument("--interval", default="1h",
                    help=f"one of {', '.join(INTERVALS)} (default 1h). "
                         f"NOTE: 1d is useless for vwapfade -- see below.")
    ap.add_argument("--start", default="2020-01-01", help="YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--out", default=None, help="output CSV path")
    args = ap.parse_args()

    try:
        import dukascopy_python
        from dukascopy_python import instruments as I
    except ImportError:
        print("Missing dependency. Run:  pip install dukascopy-python")
        return 1

    if args.instrument not in INSTRUMENTS:
        print(f"Unknown instrument '{args.instrument}'. Known: {', '.join(INSTRUMENTS)}")
        return 1
    if args.interval not in INTERVALS:
        print(f"Unknown interval '{args.interval}'. Known: {', '.join(INTERVALS)}")
        return 1

    inst_name = INSTRUMENTS[args.instrument]
    if not hasattr(I, inst_name):
        print(f"This version of dukascopy-python has no '{inst_name}'.")
        print("Close matches it DOES have:")
        key = args.instrument.split("_")[0][:3].upper()
        for n in [x for x in dir(I) if x.startswith("INSTRUMENT_") and key in x][:10]:
            print("   ", n)
        return 1

    start = datetime.datetime.strptime(args.start, "%Y-%m-%d")
    end = (datetime.datetime.strptime(args.end, "%Y-%m-%d") if args.end
           else datetime.datetime.utcnow())

    # Loud, because it is the single most common way to waste an afternoon here:
    # daily bars cannot produce a single vwapfade trade. On a daily bar the whole
    # session IS the bar, so the session VWAP collapses to that bar's own average
    # price and "close 2 ATR below it" never happens. Verified on 15 years of real
    # Nasdaq data: 1,282 trades on hourly bars, 0 on daily.
    if args.interval == "1d":
        print("WARNING: interval=1d produces ZERO trades for vwapfade. That "
              "strategy needs intraday bars (1h or finer). Continuing anyway.")

    print(f">> {args.instrument} ({inst_name}) @ {args.interval}")
    print(f">> {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    print(">> fetching (this can take a few minutes for multi-year ranges)")

    try:
        df = dukascopy_python.fetch(
            getattr(I, inst_name),
            getattr(dukascopy_python, INTERVALS[args.interval]),
            dukascopy_python.OFFER_SIDE_BID,
            start, end,
        )
    except Exception as exc:                      # network, proxy, bad symbol
        print(f"FETCH FAILED: {type(exc).__name__}: {exc}")
        print("If this says 403 / proxy, you are on a machine without access to "
              "dukascopy.com -- run it somewhere with normal internet.")
        return 1

    if df is None or len(df) == 0:
        print("No rows returned. Check the date range and instrument.")
        return 1

    out = args.out or os.path.join(
        "fixtures", "data",
        f"{args.instrument}_{args.interval}_{start:%Y%m%d}_{end:%Y%m%d}.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    # Write the plain t,o,h,l,c,v shape data.load_csv() reads without guessing.
    # Timestamps go out as UTC epoch seconds so no timezone can be misread later
    # -- a mis-parsed time column silently produces "no setups" rather than an
    # error, which is exactly how a real export wasted a session already.
    cols = {c.lower(): c for c in df.columns}
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        for ts, row in df.iterrows():
            w.writerow([
                int(ts.timestamp()),
                row[cols["open"]], row[cols["high"]],
                row[cols["low"]], row[cols["close"]],
                row[cols["volume"]] if "volume" in cols else 0,
            ])

    print(f">> wrote {len(df):,} bars to {out}")
    print()
    print("Now run:")
    print(f'   python run.py "vwap fade" --data {out} --fee 1')
    return 0


if __name__ == "__main__":
    sys.exit(main())
