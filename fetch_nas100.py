"""
fetch_nas100.py - Rebuild the NAS100 1-hour history used for validation.

WHY THIS EXISTS INSTEAD OF A COMMITTED CSV
The source is FutureSharks/financial-data, which is GPL-3. This repo is
private-use and is not GPL, so the derived bars are regenerated on demand
rather than vendored in. It is also 4MB of data that a script reproduces
exactly, which is not something a git history needs.

WHAT IT GIVES YOU
NAS100 (Nasdaq 100 CFD, Oanda feed) 1-minute bars from 2005-01 to 2020-05,
aggregated to 1 hour: 92,506 bars over 15.4 years. That is the deepest
sample this project has ever had, and it spans 2008, the 2010-2020 bull
run, the 2015 and 2018 corrections, and the start of COVID -- i.e. several
genuinely different volatility regimes, which is the point.

WHAT IT DOES NOT GIVE YOU
Anything after 2020-05. Recent intraday index data is the commercially
valuable window and is not casually published, so the recent out-of-sample
has to come from a Dukascopy export instead (see fixtures/data/).

Usage:
    python fetch_nas100.py [output.csv]
"""
from __future__ import annotations

import csv
import glob
import os
import subprocess
import sys

import data as D

SRC_REPO = "https://github.com/FutureSharks/financial-data"
SRC_DIR = os.path.expanduser("~/futuresharks/financial-data")
GLOB = "pyfinancialdata/data/currencies/oanda/NAS100_USD/*/*.csv"
DEFAULT_OUT = "fixtures/data_nas100_1h/NAS100_1H_2005-2020.csv"


def ensure_source() -> str:
    if not os.path.isdir(os.path.join(SRC_DIR, ".git")):
        print(f">> cloning {SRC_REPO} (~200MB, one time)")
        os.makedirs(os.path.dirname(SRC_DIR), exist_ok=True)
        env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")
        subprocess.run(["git", "clone", "--depth", "1", SRC_REPO, SRC_DIR],
                       check=True, env=env)
    return SRC_DIR


def build(out_path: str = DEFAULT_OUT, seconds: int = 3600) -> str:
    src = ensure_source()
    paths = sorted(glob.glob(os.path.join(src, GLOB)))
    if not paths:
        raise SystemExit(f"no NAS100 files under {src}/{GLOB}")
    print(f">> reading {len(paths)} monthly files")
    bars = D.load_csv_many(paths)
    print(f"   1-minute bars: {len(bars):,}")
    agg = D.resample(bars, seconds)
    print(f"   {seconds}s bars:  {len(agg):,}")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        for b in agg:
            w.writerow([int(b.t), b.o, b.h, b.l, b.c, b.v])
    print(f">> wrote {out_path}")
    return out_path


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
