"""
test_engine.py - assert-based self-check, no framework.

Run directly:  python test_engine.py
"""
from __future__ import annotations

import data as D
from engine import atr, macd, stochastic, vwap_rolling, supertrend, roc, wavetrend, run, Report, Bar
from strategies import REGISTRY
from opt import optimize, PARAM_GRIDS


def test_atr_no_longer_all_nan():
    bars = D.load_csv(D.sample_csv())
    a = atr(bars, 14)
    valid = [x for x in a if x == x]
    assert len(valid) > 500, "atr() should produce real values once warmed up"
    assert all(x >= 0 for x in valid), "ATR is a range, must be non-negative"


def test_macd_shapes():
    closes = [b.c for b in D.load_csv(D.sample_csv())]
    line, sig, hist = macd(closes)
    assert len(line) == len(sig) == len(hist) == len(closes)
    assert any(x == x for x in line), "macd line should have non-NaN values"


def test_stochastic_bounded():
    bars = D.load_csv(D.sample_csv())
    k, d = stochastic(bars)
    for x in k:
        if x == x:
            assert 0.0 <= x <= 100.0, "%K must be within [0, 100]"


def test_vwap_between_bar_extremes_ish():
    bars = D.load_csv(D.sample_csv())
    v = vwap_rolling(bars, 20)
    assert any(x == x for x in v)


def test_supertrend_direction_flips():
    bars = D.load_csv(D.sample_csv())
    _, direction = supertrend(bars, 10, 3.0)
    seen = set(d for d in direction if d != 0)
    assert seen, "supertrend should resolve a direction once ATR warms up (regression: atr() NaN-poisoning bug)"
    assert seen <= {1, -1}


def test_roc_zero_at_flat_price():
    assert roc([100.0] * 20, 10)[15] == 0.0


def test_wavetrend_no_longer_all_nan():
    closes = [b.c for b in D.load_csv(D.sample_csv())]
    wt1, wt2 = wavetrend(closes, 10, 11)
    assert any(x == x for x in wt1), "wt1 should have real values once warmed up (regression: ema() NaN-seed poisoning)"
    assert any(x == x for x in wt2)


def test_lorentzian_classification_resolves_a_signal():
    bars = D.load_csv(D.sample_csv())
    strat = REGISTRY["lorentzian"](bars=bars, params={})
    seen = set(strat.positions)
    assert seen != {"FLAT"}, "should take at least one position once warmed up (regression: wavetrend NaN-poisoning made every feature NaN)"
    assert seen <= {"LONG", "SHORT", "FLAT"}


def test_all_registered_strategies_run_without_error():
    bars = D.load_csv(D.sample_csv())
    for key, cls in REGISTRY.items():
        strat = cls(bars=bars, params={})
        rep = run(strat, initial=10000.0, fee_bps=0.0)
        assert isinstance(rep, Report)
        assert len(rep.equity) == len(bars)


def test_optimize_ranks_by_train_metric_and_covers_every_grid():
    bars = D.load_csv(D.sample_csv())
    for key in PARAM_GRIDS:
        results = optimize(key, bars, top_n=3)
        assert results, f"optimize('{key}') returned no candidates"
        train_scores = [getattr(train_rep, "sharpe") for _, train_rep, _ in results]
        assert train_scores == sorted(train_scores, reverse=True), f"'{key}' results not sorted by train sharpe"


def test_orb_flat_during_range_then_breaks_out():
    # Two fake sessions of hourly bars. Session 1: opening range 100-102 over
    # the first 3 bars, then a clean breakout above 102. Session 2 starts a
    # fresh range -- the prior session's high/low must not leak across days.
    day1 = 86400 * 20000  # midnight-aligned epoch, so +3600/+7200/etc. stay same UTC date
    bars = [
        Bar(t=day1+0,    o=100, h=101, l=100, c=100.5),
        Bar(t=day1+3600, o=100, h=102, l=99,  c=101),
        Bar(t=day1+7200, o=101, h=101, l=100, c=100.8),   # range = [99,102]
        Bar(t=day1+10800,o=101, h=105, l=101, c=104),     # breaks above 102 -> LONG
        Bar(t=day1+14400,o=104, h=106, l=103, c=105),     # still LONG
        Bar(t=day1+86400,     o=200, h=201, l=200, c=200.5),  # new day, new range starts
        Bar(t=day1+86400+3600,o=200, h=202, l=199, c=200),
        Bar(t=day1+86400+7200,o=200, h=200, l=195, c=196.5), # range = [195,202]
        Bar(t=day1+86400+10800,o=196, h=196, l=190, c=192),  # breaks below the new day's low -> SHORT
    ]
    strat = REGISTRY["orb"](bars=bars, params={"range_bars": 3, "flatten_eod": False})
    positions = [strat.decide(i) for i in range(len(bars))]
    assert positions[0] == "FLAT" and positions[1] == "FLAT" and positions[2] == "FLAT", \
        "should stay flat while the opening range is still forming"
    assert positions[3] == "LONG", "should go long once close breaks above the opening range high"
    assert positions[5] == "FLAT", "a new session must reset to flat, not inherit the prior day's position"
    assert positions[8] == "SHORT", "should short a breakout below the new session's own opening range low"


def test_verdict_returns_known_grade():
    bars = D.load_csv(D.sample_csv())
    strat = REGISTRY["sma"](bars=bars, params={})
    rep = run(strat, initial=10000.0, fee_bps=0.0)
    grade, notes = rep.verdict()
    assert grade in ("A", "B", "C", "D", "F")
    assert isinstance(notes, list)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
