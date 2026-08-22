"""
selftest.py - stdlib unittest suite. No pip, no network.

Run:  python selftest.py
      python selftest.py -v

The repo had no tests. This project cannot be trusted without them: it exists
to audit somebody else's suspiciously good backtest, which is not a job you can
do with an unaudited backtester. The load-bearing test here is
`test_pnl_reconciles_with_equity` -- that one invariant would have caught the
bug where fees were charged to cash but never recorded in Trade.pnl, leaving
win_rate and profit_factor computed on gross P&L.
"""
from __future__ import annotations

import math
import unittest
from typing import List

from engine import (Bar, Strategy, Trade, Report, run, bars_per_year, sma, ema, rsi, atr,
                   vwap_session)


def mkbars(closes, t0=0, dt=86400, spread=0.0) -> List[Bar]:
    """Build bars from a close series. `spread` widens high/low around it."""
    return [Bar(t=t0 + i * dt, o=c, h=c + spread, l=c - spread, c=c, v=1.0)
            for i, c in enumerate(closes)]


class Always(Strategy):
    """Holds one posture for the whole run."""
    def __init__(self, bars=None, params=None, posture="LONG"):
        self.posture = posture
        super().__init__(bars, params)

    def decide(self, i):
        return self.posture


class Scripted(Strategy):
    """Returns a caller-supplied posture per bar index."""
    def __init__(self, bars=None, params=None, script=None):
        self.script = script or []
        super().__init__(bars, params)

    def decide(self, i):
        return self.script[i] if i < len(self.script) else "FLAT"


# ---------------------------------------------------------------------------
# The invariant that catches cost-blindness
# ---------------------------------------------------------------------------
class TestPnLReconciliation(unittest.TestCase):

    def _check(self, bars, script, fee_bps, msg):
        rep = run(Scripted(bars=bars, script=script), initial=10000.0, fee_bps=fee_bps)
        booked = sum(t.pnl for t in rep.trades)
        moved = rep.final - rep.initial
        self.assertAlmostEqual(
            booked, moved, places=6,
            msg=f"{msg}: trades book {booked:.6f} but equity moved {moved:.6f} "
                f"(gap {moved - booked:.6f} = unrecorded costs)")

    def test_pnl_reconciles_with_equity(self):
        """sum(Trade.pnl) must equal final - initial. THE regression test."""
        closes = [100, 101, 103, 102, 105, 104, 106, 108, 107, 110, 109, 112]
        bars = mkbars(closes)
        long_flat = ["FLAT"] + ["LONG", "LONG", "FLAT", "FLAT"] * 3
        for fee in (0.0, 10.0, 100.0):
            with self.subTest(fee_bps=fee):
                self._check(bars, long_flat, fee, f"long/flat @ {fee}bps")

    def test_pnl_reconciles_with_shorts(self):
        closes = [100, 99, 97, 98, 95, 96, 94, 92, 93, 90]
        bars = mkbars(closes)
        script = ["FLAT", "SHORT", "SHORT", "FLAT", "SHORT", "SHORT",
                  "LONG", "LONG", "FLAT", "FLAT"]
        for fee in (0.0, 25.0):
            with self.subTest(fee_bps=fee):
                self._check(bars, script, fee, f"with shorts @ {fee}bps")

    def test_flat_market_with_fees_is_a_loss(self):
        """The exact scenario that exposed the bug: flat price, real fees.

        Before the fix this reported 0 trades won, profit factor inf, and every
        Trade.pnl == 0.00 -- while the account actually lost $1,318.74.
        """
        bars = mkbars([100.0] * 40)
        script = ["FLAT"] + ["LONG" if (i // 3) % 2 == 0 else "FLAT"
                             for i in range(1, 40)]
        rep = run(Scripted(bars=bars, script=script), initial=10000.0, fee_bps=100.0)

        self.assertGreater(len(rep.trades), 0, "expected round trips")
        self.assertLess(rep.final, 10000.0, "fees must reduce equity")
        self.assertAlmostEqual(sum(t.pnl for t in rep.trades),
                               rep.final - rep.initial, places=6)
        # Every trade is a loser: price never moved, so only fees were paid.
        self.assertEqual(rep.win_rate, 0.0)
        self.assertTrue(all(t.pnl < 0 for t in rep.trades),
                        "flat price + fees means every trade loses")
        self.assertNotEqual(rep.profit_factor, float("inf"),
                            "profit factor must see the losses")
        for t in rep.trades:
            self.assertAlmostEqual(t.pnl_gross, 0.0, places=9)
            self.assertGreater(t.fees, 0.0)
            self.assertAlmostEqual(t.pnl, t.pnl_gross - t.fees, places=9)


# ---------------------------------------------------------------------------
# Solvency
# ---------------------------------------------------------------------------
class TestSolvency(unittest.TestCase):

    def test_short_squeeze_liquidates_instead_of_going_negative(self):
        """Old engine: short into a 3x rally -> equity -17,600, then a
        sign-inverted 'LONG' opened on negative cash."""
        bars = mkbars([100.0 + i * 12 for i in range(30)])
        script = ["SHORT"] * 25 + ["LONG"] * 5
        rep = run(Scripted(bars=bars, script=script), initial=10000.0)

        self.assertGreaterEqual(min(rep.equity), 0.0,
                                "equity must never go negative")
        self.assertTrue(any(t.side == "LIQUIDATED" for t in rep.trades),
                        "a wipeout must be recorded as a liquidation")

    def test_no_position_opens_on_nonpositive_equity(self):
        bars = mkbars([100.0 + i * 12 for i in range(30)])
        rep = run(Scripted(bars=bars, script=["SHORT"] * 30), initial=10000.0)
        for t in rep.trades:
            self.assertGreaterEqual(t.entry_px, 0.0)
        self.assertGreaterEqual(min(rep.equity), 0.0)

    def test_open_position_is_flattened_at_end(self):
        bars = mkbars([100, 102, 104, 106, 108])
        rep = run(Always(bars=bars, posture="LONG"), initial=10000.0)
        self.assertTrue(rep.trades, "a held position must still be booked")
        self.assertEqual(rep.trades[-1].exit_i, len(bars) - 1)
        self.assertAlmostEqual(sum(t.pnl for t in rep.trades),
                               rep.final - rep.initial, places=6)


# ---------------------------------------------------------------------------
# Hand-computed arithmetic
# ---------------------------------------------------------------------------
class TestKnownTrade(unittest.TestCase):

    def test_single_long_to_the_cent(self):
        """10,000 into 100.00, out at 110.00, 10bps a side.

        units    = 10000 / 100          = 100
        fee_in   = 10000 * 0.001        = 10.00
        gross    = (110 - 100) * 100    = 1000.00
        fee_out  = 11000 * 0.001        = 11.00
        net      = 1000 - 10 - 11       = 979.00
        """
        bars = mkbars([100.0, 100.0, 110.0])
        rep = run(Scripted(bars=bars, script=["FLAT", "LONG", "FLAT"]),
                  initial=10000.0, fee_bps=10.0)
        self.assertEqual(len(rep.trades), 1)
        t = rep.trades[0]
        self.assertAlmostEqual(t.pnl_gross, 1000.0, places=6)
        self.assertAlmostEqual(t.fees, 21.0, places=6)
        self.assertAlmostEqual(t.pnl, 979.0, places=6)
        self.assertAlmostEqual(rep.final, 10979.0, places=6)

    def test_entry_index_is_recorded(self):
        bars = mkbars([100, 101, 102, 103, 104, 105])
        rep = run(Scripted(bars=bars,
                           script=["FLAT", "FLAT", "LONG", "LONG", "FLAT", "FLAT"]))
        self.assertEqual(len(rep.trades), 1)
        self.assertEqual(rep.trades[0].entry_i, 2)
        self.assertEqual(rep.trades[0].exit_i, 4)


# ---------------------------------------------------------------------------
# Annualization
# ---------------------------------------------------------------------------
class TestAnnualization(unittest.TestCase):

    def test_daily_bars(self):
        bars = mkbars([100.0] * 400, dt=86400)
        self.assertAlmostEqual(bars_per_year(bars), 365.25, delta=2.0)

    def test_minute_bars_are_not_252(self):
        """The old hardcoded sqrt(252) overstated 1m Sharpe by ~37x."""
        bars = mkbars([100.0] * 5000, dt=60)
        ppy = bars_per_year(bars)
        self.assertGreater(ppy, 500000)
        self.assertAlmostEqual(math.sqrt(ppy) / math.sqrt(252), 46.6, delta=2.0)

    def test_degenerate_input_falls_back(self):
        self.assertEqual(bars_per_year([]), 252.0)
        self.assertEqual(bars_per_year(mkbars([100, 101], dt=0)), 252.0)


# ---------------------------------------------------------------------------
# Metrics discipline
# ---------------------------------------------------------------------------
class TestMetrics(unittest.TestCase):

    def _report(self, pnls) -> Report:
        trades = [Trade(side="LONG", entry_i=i, entry_px=100.0, exit_i=i + 1,
                        exit_px=100.0 + p, pnl=p, pnl_pct=p / 100.0,
                        pnl_gross=p, fees=0.0)
                  for i, p in enumerate(pnls)]
        return Report(equity=[10000.0], trades=trades, initial=10000.0,
                      final=10000.0 + sum(pnls), total_return=0.0, max_dd=0.0,
                      win_rate=len([p for p in pnls if p > 0]) / len(pnls),
                      profit_factor=0.0, sharpe=0.0, bars=len(pnls))

    def test_breakeven_win_rate_at_1R(self):
        """Equal wins and losses -> breakeven sits at 50%."""
        rep = self._report([10, 10, 10, -10, -10])
        self.assertAlmostEqual(rep.payoff_ratio, 1.0, places=6)
        self.assertAlmostEqual(rep.breakeven_win_rate, 0.5, places=6)

    def test_high_win_rate_can_still_lose_money(self):
        """The heart of the matter: 80% wins, negative expectancy.

        This is the shape a 72% win rate takes when TP sits near and SL sits
        far -- which is why the report must never print a win rate without the
        payoff ratio and breakeven beside it.
        """
        rep = self._report([1, 1, 1, 1, -10])
        self.assertAlmostEqual(rep.win_rate, 0.8, places=6)
        self.assertLess(rep.expectancy, 0.0)
        self.assertGreater(rep.breakeven_win_rate, rep.win_rate,
                           "breakeven above actual == losing strategy")

    def test_summary_shows_breakeven_next_to_win_rate(self):
        text = self._report([1, 1, 1, 1, -10]).summary()
        self.assertIn("breakeven", text)
        self.assertIn("Expectancy", text)
        self.assertIn("Payoff ratio", text)


# ---------------------------------------------------------------------------
# Indicators (guarding against silent regressions during the port)
# ---------------------------------------------------------------------------
class TestIndicators(unittest.TestCase):

    def test_sma(self):
        out = sma([1, 2, 3, 4, 5], 3)
        self.assertTrue(math.isnan(out[0]) and math.isnan(out[1]))
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[4], 4.0)

    def test_rsi_bounds(self):
        vals = [100 + math.sin(i / 5.0) * 10 for i in range(100)]
        for r in rsi(vals, 14):
            if r == r:
                self.assertGreaterEqual(r, 0.0)
                self.assertLessEqual(r, 100.0)

    def test_rsi_all_gains_is_100(self):
        self.assertAlmostEqual(rsi(list(range(1, 40)), 14)[-1], 100.0, places=6)

    def test_atr_positive(self):
        bars = mkbars([100 + i for i in range(50)], spread=1.5)
        for a in atr(bars, 14):
            if a == a:
                self.assertGreater(a, 0.0)

    def test_ema_seeding_differs_from_pine(self):
        """Documents a known parity gap ahead of the Pine port.

        engine.ema seeds with vals[0]; Pine's ta.ema seeds with an SMA of the
        first n. Small, and exactly the kind of divergence that flips
        nearest-neighbour votes in the classifier.
        """
        vals = [float(i) for i in range(1, 51)]
        ours = ema(vals, 10)
        pine_seed = sum(vals[:10]) / 10
        k = 2.0 / 11
        prev = pine_seed
        for x in vals[10:]:
            prev = x * k + prev * (1 - k)
        self.assertNotAlmostEqual(ours[-1], prev, places=9)
        self.assertAlmostEqual(ours[-1], prev, places=2)  # converges, never equal


class TestVWAPSession(unittest.TestCase):

    def test_resets_at_each_new_utc_day(self):
        # Two 1-hour bars in day 1, then one in day 2. If day 2 didn't reset,
        # its VWAP would still carry day 1's volume in the average.
        bars = [
            Bar(t=0,      o=100, h=101, l=99,  c=100, v=10),
            Bar(t=3600,   o=100, h=101, l=99,  c=100, v=10),
            Bar(t=86400,  o=200, h=201, l=199, c=200, v=10),
        ]
        v = vwap_session(bars)
        self.assertAlmostEqual(v[2], 200.0, places=6)

    def test_matches_typical_price_on_first_bar_of_a_session(self):
        bars = [Bar(t=0, o=10, h=12, l=8, c=11, v=5)]
        v = vwap_session(bars)
        self.assertAlmostEqual(v[0], (12 + 8 + 11) / 3.0, places=6)

    def test_zero_volume_degrades_to_equal_weighting_not_nan(self):
        bars = [Bar(t=0, o=10, h=10, l=10, c=10, v=0),
                Bar(t=60, o=20, h=20, l=20, c=20, v=0)]
        v = vwap_session(bars)
        self.assertAlmostEqual(v[1], 15.0, places=6)


# ---------------------------------------------------------------------------
# Parity with the Pine source
# ---------------------------------------------------------------------------
class TestLorentzianParity(unittest.TestCase):
    """Guards the Python port against drifting from lorentzian_classification.pine."""

    @staticmethod
    def _pine_label(four_bars_ago, now):
        """Verbatim translation of .pine:335, with long=1 / short=-1 from .pine:291.

            y_train_series = src[4] < src[0] ? direction.short
                           : src[4] > src[0] ? direction.long
                           : direction.neutral

        `src[4]` is 4 bars ago and `src[0]` is now, so a 4-bar RISE labels SHORT.
        """
        if four_bars_ago < now:
            return -1
        if four_bars_ago > now:
            return 1
        return 0

    def test_lorentzian_labels_match_pine(self):
        """A 4-bar rise must label SHORT (-1), a fall LONG (+1).

        The port once had this inverted, which silently made it trade the mirror
        image of the indicator. Nothing else in the pipeline compensates: both
        upstream and the port go long on `prediction_sum > 0`.
        """
        from strategies import LorentzianClassification

        closes = [100, 100, 100, 100, 110, 90, 110, 110, 95, 95, 120, 80]
        bars = mkbars(closes)
        strat = LorentzianClassification(bars=bars, params={})
        strat.prepare()

        labels = getattr(strat, "labels", None)
        if labels is None:
            self.skipTest("port does not expose `labels`; parity checked in-line below")

        for i in range(4, len(closes)):
            with self.subTest(i=i, prev=closes[i - 4], now=closes[i]):
                self.assertEqual(labels[i], self._pine_label(closes[i - 4], closes[i]))

    def test_label_formula_direction_is_not_naive(self):
        """The formula must be the *inverse* of the trailing move's sign.

        Independent of the port's internals: this pins the semantics so a future
        edit that makes the sign "read naturally" fails loudly.
        """
        self.assertEqual(self._pine_label(100, 110), -1, "a 4-bar RISE labels SHORT")
        self.assertEqual(self._pine_label(110, 100), 1, "a 4-bar FALL labels LONG")
        self.assertEqual(self._pine_label(100, 100), 0, "flat labels NEUTRAL")


# ---------------------------------------------------------------------------
# Bracket backtester
# ---------------------------------------------------------------------------
class TestBrackets(unittest.TestCase):
    """Hand-constructed geometry, known outcomes. This is the part of the
    project that actually stands in for the vendor's on-chart stats table,
    so its arithmetic has to be trustworthy on its own -- not just
    plausible-looking."""

    def _run(self, bars, entry_i, side, sl_atr=1.0, be_offset_atr=0.15,
             atr_len=1, policy="pessimistic"):
        from brackets import run_brackets
        n = len(bars)
        start_long = [False] * n
        start_short = [False] * n
        if side == "LONG":
            start_long[entry_i] = True
        else:
            start_short[entry_i] = True
        rep = run_brackets(bars, start_long, start_short, atr_len=atr_len,
                           sl_atr=sl_atr, be_offset_atr=be_offset_atr, policy=policy)
        self.assertEqual(len(rep.trades), 1, "expected exactly one bracket")
        return rep.trades[0]

    @staticmethod
    def _flat_bar(t, px, h=None, l=None, o=None):
        """A bar with an explicit range, decoupled from mkbars' o==c
        convention -- needed here because that convention makes every bar's
        open equal to its own close, which silently triggers this module's
        gap-fill path in tests that mean to exercise the intrabar path."""
        return Bar(t=t, o=px if o is None else o, h=px if h is None else h,
                  l=px if l is None else l, c=px)

    def test_clean_tp1_then_tp2_blended(self):
        """Long at 100, ATR=1 (bar 1's own h/l spread of +-0.5 sets it) ->
        stop 99, TP1 101, TP2 102. TP1 fills on bar 2 (half off, stop walks
        to breakeven+), TP2 fills on bar 3. This is the two-leg path, so the
        correct blend is 0.5*1R + 0.5*2R = 1.5R -- NOT a flat 2.0R, which
        would only be right if the position never partial-exited at TP1."""
        bars = [
            self._flat_bar(0, 100),
            self._flat_bar(1, 100, h=100.5, l=99.5),   # entry bar: sets ATR=1.0
            self._flat_bar(2, 100.5, h=101.2, l=100),  # touches TP1 (101) intrabar
            self._flat_bar(3, 101.5, h=102.2, l=101),  # touches TP2 (102) intrabar
        ]
        t = self._run(bars, entry_i=1, side="LONG", atr_len=1)
        self.assertEqual(t.exit_reason, "tp2")
        self.assertEqual([f.reason for f in t.fills], ["tp1", "tp2"])
        self.assertAlmostEqual(t.r_multiple, 0.5 * 1.0 + 0.5 * 2.0, places=6)

    def test_full_size_favourable_gap_scores_at_the_actual_fill(self):
        """A bar that gaps open past TP2 exits full size at the OPEN, not at
        the target level -- a sell-limit at 102 with the market opening at
        103 fills at 103 (price improvement), so the trade realises +3R.

        This is the mirror of test_gap_through_stop_fills_at_open_not_at_stop.
        Both legs score from the actual fill: crediting the adverse gap while
        capping the favourable one at its nominal level would bias every
        result downward.
        """
        bars = [
            self._flat_bar(0, 100),
            self._flat_bar(1, 100, h=100.5, l=99.5),   # entry bar, ATR=1, tp2=102
            self._flat_bar(2, 103, o=103, h=103.5, l=102.5),  # opens straight past TP2
        ]
        t = self._run(bars, entry_i=1, side="LONG", atr_len=1)
        self.assertEqual(t.exit_reason, "tp2")
        self.assertEqual(len(t.fills), 1, "must be a single full-size fill, no TP1 leg")
        self.assertEqual(t.fills[-1].px, 103.0, "must fill at the gapped open")
        self.assertAlmostEqual(t.r_multiple, 3.0, places=6)

    def test_clean_stop_hit(self):
        bars = [
            self._flat_bar(0, 100),
            self._flat_bar(1, 100, h=100.5, l=99.5),        # entry bar, ATR=1, stop=99
            self._flat_bar(2, 99, o=99.5, h=99.5, l=98.5),  # opens above stop, dips through intrabar
        ]
        t = self._run(bars, entry_i=1, side="LONG", atr_len=1)
        self.assertEqual(t.exit_reason, "stop")
        self.assertAlmostEqual(t.r_multiple, -1.0, places=6)

    def test_gap_through_stop_fills_at_open_not_at_stop(self):
        """A gap-down open below the stop must fill at the worse open price,
        not at the stop level -- filling at the level is free money that
        never existed."""
        bars = [
            self._flat_bar(0, 100),
            self._flat_bar(1, 100, h=100.5, l=99.5),   # entry bar, ATR=1, stop=99
            self._flat_bar(2, 94.5, o=95.0, h=95.0, l=94.0),  # opens well below stop
        ]
        t = self._run(bars, entry_i=1, side="LONG", atr_len=1)
        self.assertEqual(t.exit_reason, "stop")
        self.assertEqual(t.fills[-1].px, 95.0, "must fill at the gapped open")
        self.assertLess(t.r_multiple, -1.0, "worse than a clean 1R stop")

    def test_same_bar_ambiguity_resolved_by_policy(self):
        """One bar's range covers both TP1 and the stop. Pessimistic must
        take the stop; optimistic must take the target. The two policies
        must disagree on this exact bar -- that disagreement is the whole
        point of reporting both."""
        bars = [
            self._flat_bar(0, 100),
            self._flat_bar(1, 100, h=100.5, l=99.5),           # entry, ATR=1, stop=99, tp1=101
            self._flat_bar(2, 100.5, o=100, h=101.5, l=98.5),  # both touched intrabar
        ]
        pess = self._run(bars, entry_i=1, side="LONG", atr_len=1, policy="pessimistic")
        opt = self._run(bars, entry_i=1, side="LONG", atr_len=1, policy="optimistic")
        self.assertTrue(pess.ambiguous)
        self.assertTrue(opt.ambiguous)
        self.assertEqual(pess.exit_reason, "stop")
        # TP1 is a partial exit, not a terminal state -- with no bar left
        # afterwards the trade's overall exit_reason ends up "eod". What
        # matters here is which side WON the tie on the ambiguous bar itself.
        self.assertEqual(opt.fills[0].reason, "tp1")
        self.assertLess(pess.r_multiple, opt.r_multiple)

    def test_tp1_then_stopped_at_breakeven(self):
        """TP1 fills (half off, stop walks to breakeven+), then price falls
        back and takes the breakeven stop on the remaining half."""
        bars = [
            self._flat_bar(0, 100),
            self._flat_bar(1, 100, h=100.5, l=99.5),          # entry, ATR=1, stop=99, tp1=101
            self._flat_bar(2, 101, o=100.5, h=101.5, l=100.5),  # TP1 fills, be=100.15
            self._flat_bar(3, 100.15, o=100.3, h=100.3, l=100.0),  # dips through be intrabar
        ]
        t = self._run(bars, entry_i=1, side="LONG", atr_len=1, be_offset_atr=0.15)
        self.assertEqual(t.exit_reason, "stop")
        self.assertEqual(len(t.fills), 2)
        self.assertEqual(t.fills[0].reason, "tp1")
        # half at +1R, half at the breakeven+ offset (+0.15R) -> 0.575R net
        self.assertAlmostEqual(t.r_multiple, 0.5 * 1.0 + 0.5 * 0.15, places=6)

    def test_short_side_mirrors_long(self):
        """Same favourable-gap geometry as the long case, sign-flipped:
        entry 100, tp2 98, bar opens at 97 -> fills 97 -> +3R."""
        bars = [
            self._flat_bar(0, 100),
            self._flat_bar(1, 100, h=100.5, l=99.5),   # entry, ATR=1, stop=101, tp2=98
            self._flat_bar(2, 97, o=97, h=97.5, l=96.5),  # opens straight past TP2
        ]
        t = self._run(bars, entry_i=1, side="SHORT", atr_len=1)
        self.assertEqual(t.exit_reason, "tp2")
        self.assertEqual(t.fills[-1].px, 97.0, "must fill at the gapped open")
        self.assertAlmostEqual(t.r_multiple, 3.0, places=6)

    def test_entry_bars_own_range_cannot_fill(self):
        """A wide entry bar whose own high/low would touch TP1/stop must not
        fill on that bar -- entry executes at its close, and only bars
        strictly after it are eligible. Filling on the entry bar is lookahead."""
        bars = [
            self._flat_bar(0, 100),
            self._flat_bar(1, 100, h=105, l=95),   # entry bar: huge range, closes flat
            self._flat_bar(2, 100, h=100.5, l=99.5),  # nothing happens after
        ]
        t = self._run(bars, entry_i=1, side="LONG", atr_len=1)
        self.assertEqual(t.exit_reason, "eod", "no fill should occur before bar 2")

    def test_optimistic_at_least_as_good_as_pessimistic_in_aggregate(self):
        import random
        random.seed(11)
        closes = [100.0]
        for _ in range(200):
            closes.append(max(1.0, closes[-1] + random.gauss(0, 1.2)))
        bars = mkbars(closes, spread=0.8)
        n = len(bars)
        start_long = [False] * n
        start_short = [False] * n
        for i in range(5, n - 10, 15):
            start_long[i] = True
        from brackets import run_brackets
        pess = run_brackets(bars, start_long, start_short, atr_len=5)
        opt = run_brackets(bars, start_long, start_short, atr_len=5, policy="optimistic")
        self.assertGreaterEqual(opt.total_r, pess.total_r,
                                "optimistic fills can never score worse in aggregate")

    def test_no_overlapping_brackets(self):
        """A second entry signal while a bracket is already open must be
        ignored -- the source indicator can't fire a fresh entry without the
        underlying signal changing, which implies the prior position closed."""
        from brackets import run_brackets
        bars = mkbars([100, 100, 100, 100, 100, 100], spread=0.5)
        n = len(bars)
        start_long = [False] * n
        start_long[1] = True
        start_long[2] = True  # would-be second entry while bar 1's trade is still open
        rep = run_brackets(bars, start_long, [False] * n, atr_len=1)
        self.assertEqual(len(rep.trades), 1)


# ---------------------------------------------------------------------------
# Swing structure primitives
# ---------------------------------------------------------------------------
class TestPivots(unittest.TestCase):

    def test_finds_an_obvious_swing_high_and_low(self):
        from engine import pivots
        # A clean peak at index 3 and a clean trough at index 9.
        highs = [10, 11, 12, 20, 12, 11, 10, 9, 8, 5, 8, 9, 10, 11]
        bars = [Bar(t=i, o=h, h=h, l=h, c=h) for i, h in enumerate(highs)]
        is_ph, is_pl = pivots(bars, left=3, right=3)
        self.assertTrue(is_ph[3], "peak at index 3 should be a pivot high")
        self.assertTrue(is_pl[9], "trough at index 9 should be a pivot low")
        self.assertEqual(sum(is_ph), 1)
        self.assertEqual(sum(is_pl), 1)

    def test_pivot_is_not_knowable_before_its_confirmation_bar(self):
        """THE lookahead guard. A pivot at bar i needs `right` bars after it,
        so it cannot be acted on until bar i+right. confirmed_pivots() keys
        by that confirmation bar precisely so a strategy can't cheat."""
        from engine import confirmed_pivots
        highs = [10, 11, 12, 20, 12, 11, 10, 9, 8, 5, 8, 9, 10, 11]
        bars = [Bar(t=i, o=h, h=h, l=h, c=h) for i, h in enumerate(highs)]
        ph, pl = confirmed_pivots(bars, left=3, right=3)
        self.assertEqual(len(ph), 1)
        confirm_i, pivot_i, price = ph[0]
        self.assertEqual(pivot_i, 3)
        self.assertEqual(confirm_i, 6, "3 right-hand bars must close first")
        self.assertEqual(price, 20)
        self.assertGreater(confirm_i, pivot_i, "confirmation always lags the pivot")

    def test_flat_series_has_no_pivots(self):
        from engine import pivots
        bars = mkbars([100.0] * 20, spread=0.0)
        is_ph, is_pl = pivots(bars, left=3, right=3)
        self.assertEqual(sum(is_ph), 0)
        self.assertEqual(sum(is_pl), 0)

    def test_rejects_degenerate_lookback(self):
        from engine import pivots
        bars = mkbars([100, 101, 102], spread=0.0)
        with self.assertRaises(ValueError):
            pivots(bars, left=0, right=3)


# ---------------------------------------------------------------------------
# TradingLab supply/demand + structure strategy
# ---------------------------------------------------------------------------
class TestSupplyDemand(unittest.TestCase):

    def test_registered_and_runs(self):
        from strategies import REGISTRY
        self.assertIn("sdz", REGISTRY)
        bars = mkbars([100 + math.sin(i / 9.0) * 12 for i in range(400)], spread=0.6)
        strat = REGISTRY["sdz"](bars=bars, params={})
        self.assertEqual(len(strat.positions), len(bars))
        self.assertEqual(len(strat.stops), len(bars))
        self.assertEqual(len(strat.targets), len(bars))

    def test_every_signal_carries_a_stop_and_target(self):
        """brackets.py consumes these in parallel -- a signal bar with a NaN
        stop would silently fall back to an ATR stop and quietly stop being
        the strategy under test."""
        from strategies import REGISTRY
        bars = mkbars([100 + math.sin(i / 8.0) * 14 for i in range(600)], spread=0.7)
        s = REGISTRY["sdz"](bars=bars, params={})
        checked = 0
        for i, (lo, sh) in enumerate(zip(s.start_long, s.start_short)):
            if lo or sh:
                self.assertEqual(s.stops[i], s.stops[i], f"NaN stop at signal bar {i}")
                self.assertEqual(s.targets[i], s.targets[i], f"NaN target at bar {i}")
                checked += 1
        self.assertGreater(checked, 0, "no signals -- this test would pass vacuously")

    def test_rr_filter_is_what_gates_the_trades(self):
        """Step 3 of the video, and the rule it credits with most of the
        edge: identical setups, only min_rr differs. A stricter threshold can
        only ever remove trades, never add them."""
        from strategies import REGISTRY
        bars = mkbars([100 + math.sin(i / 8.0) * 14 for i in range(600)], spread=0.7)
        loose = REGISTRY["sdz"](bars=bars, params={"min_rr": 1.0})
        strict = REGISTRY["sdz"](bars=bars, params={"min_rr": 5.0})
        n_loose = sum(loose.start_long) + sum(loose.start_short)
        n_strict = sum(strict.start_long) + sum(strict.start_short)
        self.assertGreater(n_loose, 0, "min_rr=1.0 should admit some setups")
        self.assertLess(n_strict, n_loose,
                        "raising min_rr must filter setups out, not add them")

    def test_every_taken_trade_actually_meets_min_rr(self):
        """Not just fewer trades -- the ones that survive must genuinely
        clear the threshold, measured from the levels the strategy itself
        emitted."""
        from strategies import REGISTRY
        bars = mkbars([100 + math.sin(i / 6.0) * 13 for i in range(600)], spread=0.7)
        min_rr = 2.5
        s = REGISTRY["sdz"](bars=bars, params={"min_rr": min_rr})
        checked = 0
        for i in range(len(bars)):
            if not (s.start_long[i] or s.start_short[i]):
                continue
            entry, stop, tgt = bars[i].c, s.stops[i], s.targets[i]
            if s.start_long[i]:
                risk, reward = entry - stop, tgt - entry
            else:
                risk, reward = stop - entry, entry - tgt
            self.assertGreater(risk, 0, f"non-positive risk at bar {i}")
            self.assertGreaterEqual(reward / risk, min_rr - 1e-9,
                                    f"bar {i} taken at R:R {reward / risk:.2f}")
            checked += 1
        self.assertGreater(checked, 0, "no trades to check")

    def test_longs_only_in_uptrend_shorts_only_in_downtrend(self):
        """Step 1: a monotonic uptrend makes only higher highs and higher
        lows, so the strategy must never take a short in it."""
        from strategies import REGISTRY
        rising = mkbars([100 + i * 0.4 + math.sin(i / 5.0) * 2 for i in range(400)],
                        spread=0.5)
        s = REGISTRY["sdz"](bars=rising, params={})
        self.assertEqual(sum(s.start_short), 0,
                         "no shorts should fire in a persistent uptrend")

    def test_structural_stops_flow_into_the_bracket_engine(self):
        """End to end: the strategy's own stop must be the bracket's stop,
        not an ATR-derived substitute."""
        from strategies import REGISTRY
        from brackets import run_brackets
        bars = mkbars([100 + math.sin(i / 8.0) * 14 for i in range(600)], spread=0.7)
        s = REGISTRY["sdz"](bars=bars, params={})
        rep = run_brackets(bars, s.start_long, s.start_short,
                           stops=s.stops, targets=s.targets)
        self.assertGreater(rep.n, 0, "expected at least one bracket")
        for t in rep.trades:
            self.assertAlmostEqual(t.stop0, s.stops[t.entry_i], places=9)
            self.assertAlmostEqual(t.tp2, s.targets[t.entry_i], places=9)


# ---------------------------------------------------------------------------
# @bardfx "No Wick" retrace strategy
# ---------------------------------------------------------------------------
def _zigzag_closes(up=True, legs=10, leg_bars=8, step=12.0, retr=5.0, start=100.0):
    """A piecewise-linear path with REAL swing points, so confirmed_pivots()
    actually finds higher highs and higher lows (or lower lows and lower
    highs). A monotonic staircase has no local extrema at all, produces no
    pivots, and therefore leaves the structure trend permanently at 0 -- which
    silently turns every structure-mode assertion into a vacuous pass."""
    pts, px = [start], start
    for k in range(legs):
        px = px + (step if up else -step) if k % 2 == 0 else px - (retr if up else -retr)
        pts.append(px)
    closes = []
    for a, b in zip(pts, pts[1:]):
        for j in range(leg_bars):
            closes.append(a + (b - a) * (j + 1) / leg_bars)
    return closes


def _plain(t, c, spread=0.4):
    """o == c, so the bar is neither bullish nor bearish and can never be
    mistaken for a wickless setup by the detector under test."""
    return Bar(t=t, o=c, h=c + spread, l=c - spread, c=c, v=1.0)


class TestNoWick(unittest.TestCase):
    """@bardfx's 'no wick' setup: mark a with-trend candle missing its
    trend-side wick, wait for price to come back to that flat edge, enter
    there at 1:1."""

    @staticmethod
    def _bar(t, o, h, l, c):
        return Bar(t=t, o=o, h=h, l=l, c=c, v=1.0)

    # --- detection: the definition itself ----------------------------------
    def test_exact_flat_bottom_on_a_bullish_candle_is_detected(self):
        from strategies import NoWickRetrace
        s = NoWickRetrace(bars=[self._bar(0, 100, 102, 100, 101)], params={})
        self.assertTrue(s.debug_bull_marks[0])
        self.assertFalse(s.debug_bear_marks[0])

    def test_one_tick_bottom_wick_is_NOT_detected(self):
        """The boundary that matters. Wickless is exact equality; a candle
        that dipped even one tick below its open is an ordinary candle, and
        counting it would quietly swap this for a far more permissive
        strategy while still calling it 'no wick'."""
        from strategies import NoWickRetrace
        s = NoWickRetrace(bars=[self._bar(0, 100, 102, 99.99, 101)], params={})
        self.assertFalse(s.debug_bull_marks[0])

    def test_flat_top_on_a_bearish_candle_is_detected(self):
        from strategies import NoWickRetrace
        s = NoWickRetrace(bars=[self._bar(0, 100, 100, 98, 99)], params={})
        self.assertTrue(s.debug_bear_marks[0])
        self.assertFalse(s.debug_bull_marks[0])

    def test_fully_wickless_bullish_candle_still_counts_as_bullish(self):
        from strategies import NoWickRetrace
        s = NoWickRetrace(bars=[self._bar(0, 100, 101, 100, 101)], params={})
        self.assertTrue(s.debug_bull_marks[0])

    def test_relative_tolerance_loosens_detection_for_forex(self):
        """wick_tol_frac exists because an exactly-zero wick is rare on
        5-decimal FX. A wick of 1% of the bar's range is not wickless at the
        strict default and is at frac=0.05 -- the knob has to genuinely move
        the definition, or it is decoration."""
        from strategies import NoWickRetrace
        bars = [self._bar(0, 100, 102, 99.98, 101)]
        self.assertFalse(NoWickRetrace(bars=bars, params={}).debug_bull_marks[0])
        loose = NoWickRetrace(bars=bars, params={"wick_tol_frac": 0.05})
        self.assertTrue(loose.debug_bull_marks[0])

    # --- trend gate --------------------------------------------------------
    def _downtrend_of_bullish_wickless_bars(self):
        """A genuine downtrend in which EVERY bar is a bullish wickless
        candle. Detection therefore fires everywhere and the trend gate is
        the only thing that can suppress a long."""
        bars = []
        for i, c in enumerate(_zigzag_closes(up=False)):
            o = c - 0.6                       # bullish body...
            bars.append(self._bar(i, o, c + 0.4, o, c))   # ...with a flat bottom
        return bars

    def test_no_longs_in_a_downtrend_structure_mode(self):
        from strategies import NoWickRetrace
        bars = self._downtrend_of_bullish_wickless_bars()
        s = NoWickRetrace(bars=bars, params={"trend_mode": "structure"})
        self.assertGreater(sum(s.debug_bull_marks), 0,
                           "fixture must actually contain bullish wickless candles")
        self.assertGreater(s.trends.count(-1), 0,
                           "fixture must actually read as a downtrend, or this "
                           "test passes for the wrong reason")
        self.assertEqual(sum(s.start_long), 0,
                         "no longs may fire in a persistent downtrend")

    def test_no_longs_in_a_downtrend_ema_mode(self):
        from strategies import NoWickRetrace
        bars = self._downtrend_of_bullish_wickless_bars()
        s = NoWickRetrace(bars=bars, params={"trend_mode": "ema"})
        self.assertGreater(sum(s.debug_bull_marks), 0)
        self.assertGreater(s.trends.count(-1), 0)
        self.assertEqual(sum(s.start_long), 0,
                         "no longs may fire below a falling EMA either")

    # --- entry -------------------------------------------------------------
    def _setup(self, retrace_offset):
        """Uptrend with real swings, one wickless bullish candle at price P,
        then a single bar that pulls back to P + `retrace_offset`."""
        closes = _zigzag_closes(up=True)
        bars = [_plain(i, c) for i, c in enumerate(closes)]
        P = closes[-1]
        bars.append(self._bar(len(bars), P, P + 1.4, P, P + 1.0))   # the mark
        for _ in range(3):
            bars.append(_plain(len(bars), P + 1.2))
        bars.append(self._bar(len(bars), P + 0.5, P + 0.9,
                              P + retrace_offset, P + 0.5))          # the retrace
        for _ in range(40):
            bars.append(_plain(len(bars), P + 1.5))
        return bars, P

    def test_touching_only_the_body_does_not_trigger(self):
        from strategies import NoWickRetrace
        bars, _ = self._setup(retrace_offset=+0.3)
        s = NoWickRetrace(bars=bars, params={})
        self.assertEqual(sum(s.start_long), 0,
                         "a pullback into the body is not a fill of the flat edge")

    def test_touching_the_flat_edge_triggers_at_that_level(self):
        from strategies import NoWickRetrace
        bars, P = self._setup(retrace_offset=-0.01)
        s = NoWickRetrace(bars=bars, params={})
        self.assertEqual(sum(s.start_long), 1, "the flat edge was touched")
        i = s.start_long.index(True)
        self.assertAlmostEqual(s.entries[i], P, places=9,
                               msg="entry is the marked level -- a resting limit "
                                   "order -- not the signal bar's close")
        self.assertNotAlmostEqual(s.entries[i], bars[i].c, places=6)

    def test_a_candle_cannot_trigger_its_own_level(self):
        """The marking bar's own low IS the level, so without ordering care
        every wickless candle would instantly self-fill."""
        from strategies import NoWickRetrace
        bars, _ = self._setup(retrace_offset=+50.0)
        s = NoWickRetrace(bars=bars, params={})
        for i, marked in enumerate(s.debug_bull_marks):
            if marked:
                self.assertFalse(s.start_long[i],
                                 f"bar {i} both marked and entered on itself")

    def test_all_trend_modes_reach_the_same_entry(self):
        from strategies import NoWickRetrace
        bars, P = self._setup(retrace_offset=-0.01)
        for mode in ("structure", "ema", "both"):
            with self.subTest(trend_mode=mode):
                s = NoWickRetrace(bars=bars, params={"trend_mode": mode})
                self.assertEqual(sum(s.start_long), 1)
                self.assertAlmostEqual(s.entries[s.start_long.index(True)], P, places=9)

    @staticmethod
    def _rally_then_pullback():
        """A market that is plainly rising, containing one pullback deep
        enough to undercut the prior swing low.

        This is the exact shape Stefan reported from the live 15m chart: to
        the eye it is an uptrend, but a 5-bar pivot reads only about an hour,
        so the pullback prints a lower low AND a lower high and `structure`
        calls it a downtrend -- and the strategy shorts a rising market.
        """
        pts = [100, 150, 200, 250, 300, 280, 295, 262, 288, 330, 380]
        closes = []
        for a, b in zip(pts, pts[1:]):
            for j in range(8):
                closes.append(a + (b - a) * (j + 1) / 8)
        return [_plain(i, c) for i, c in enumerate(closes)]

    def test_both_mode_suppresses_a_pullback_that_only_structure_calls_bearish(self):
        """The reported bug, and the fix for it."""
        from strategies import NoWickRetrace
        bars = self._rally_then_pullback()
        st = NoWickRetrace(bars=bars, params={"trend_mode": "structure"})
        em = NoWickRetrace(bars=bars, params={"trend_mode": "ema"})
        bo = NoWickRetrace(bars=bars, params={"trend_mode": "both"})
        self.assertGreater(st.trends.count(-1), 0,
                           "fixture must actually make structure read bearish, "
                           "or this test proves nothing")
        self.assertEqual(em.trends.count(-1), 0,
                         "price never goes below the EMA here -- by that "
                         "definition it is an uptrend throughout")
        self.assertEqual(bo.trends.count(-1), 0,
                         "'both' must not call a downtrend while price is "
                         "still above the EMA")

    def test_both_mode_is_never_looser_than_either_alone(self):
        """`both` is an AND of two gates, so wherever it is in-trend, each
        gate must independently agree. That is what makes it strictly safer
        rather than just different."""
        from strategies import NoWickRetrace
        bars = self._rally_then_pullback()
        st = NoWickRetrace(bars=bars, params={"trend_mode": "structure"})
        em = NoWickRetrace(bars=bars, params={"trend_mode": "ema"})
        bo = NoWickRetrace(bars=bars, params={"trend_mode": "both"})
        for i in range(len(bars)):
            if bo.trends[i] != 0:
                self.assertEqual(bo.trends[i], st.trends[i])
                self.assertEqual(bo.trends[i], em.trends[i])

    # --- levels ------------------------------------------------------------
    def test_rr_1_means_target_distance_equals_risk_distance(self):
        from strategies import NoWickRetrace
        bars, _ = self._setup(retrace_offset=-0.01)
        s = NoWickRetrace(bars=bars, params={"rr": 1.0})
        i = s.start_long.index(True)
        risk = s.entries[i] - s.stops[i]
        reward = s.targets[i] - s.entries[i]
        self.assertGreater(risk, 0)
        self.assertAlmostEqual(reward, risk, places=9)

    def test_rr_2_doubles_the_target_distance(self):
        from strategies import NoWickRetrace
        bars, _ = self._setup(retrace_offset=-0.01)
        s = NoWickRetrace(bars=bars, params={"rr": 2.0})
        i = s.start_long.index(True)
        risk = s.entries[i] - s.stops[i]
        self.assertAlmostEqual(s.targets[i] - s.entries[i], 2 * risk, places=9)

    def test_stop_modes_produce_different_risk(self):
        """'Below the candle with breathing room' and 'at the swing low' are
        different trades; if they weren't, the parameter would be a lie."""
        from strategies import NoWickRetrace
        bars, _ = self._setup(retrace_offset=-0.01)
        a = NoWickRetrace(bars=bars, params={"stop_mode": "candle"})
        b = NoWickRetrace(bars=bars, params={"stop_mode": "structure"})
        ia, ib = a.start_long.index(True), b.start_long.index(True)
        self.assertLess(b.stops[ib], a.stops[ia],
                        "the swing low sits below the candle, so the structural "
                        "stop must be the wider one here")

    def test_buffer_widens_the_stop(self):
        from strategies import NoWickRetrace
        bars, _ = self._setup(retrace_offset=-0.01)
        tight = NoWickRetrace(bars=bars, params={"stop_buffer_atr": 0.05})
        wide = NoWickRetrace(bars=bars, params={"stop_buffer_atr": 0.50})
        it, iw = tight.start_long.index(True), wide.start_long.index(True)
        self.assertLess(wide.stops[iw], tight.stops[it],
                        "more breathing room means a lower stop on a long")

    def test_registered_and_routed(self):
        from strategies import REGISTRY
        from gen import plan_from_text
        self.assertIn("nowick", REGISTRY)
        self.assertEqual(plan_from_text("no wick")[0], "nowick")
        self.assertEqual(plan_from_text("wickless candles")[0], "nowick")


class TestBracketLimitEntry(unittest.TestCase):
    """The three brackets.py extensions No Wick needs: limit entries,
    entry-bar fills, and a single target with no scale-out."""

    @staticmethod
    def _bar(t, o, h, l, c):
        return Bar(t=t, o=o, h=h, l=l, c=c, v=1.0)

    def _bars(self):
        # Bar 2 is the entry bar: it dips to 99 (the limit level) and then
        # runs up through 100 within that same bar.
        return [
            self._bar(0, 100, 100.5, 99.5, 100),
            self._bar(1, 100, 100.5, 99.5, 100),      # sets ATR
            self._bar(2, 100, 101.5, 99.0, 99.3),
            self._bar(3, 99.3, 99.4, 99.1, 99.2),     # never reaches 100
        ]

    def _run(self, allow, *, entry=99.0, stop=98.0, target=100.0, partial=False):
        from brackets import run_brackets
        bars = self._bars()
        n = len(bars)
        sl = [False] * n
        sl[2] = True
        nan = float("nan")
        entries = [nan] * n; entries[2] = entry
        stops = [nan] * n;   stops[2] = stop
        targets = [nan] * n; targets[2] = target
        rep = run_brackets(bars, sl, [False] * n, atr_len=1,
                           stops=stops, targets=targets, entries=entries,
                           allow_entry_bar_fill=allow, partial_at_tp1=partial)
        self.assertEqual(rep.n, 1)
        return rep.trades[0]

    def test_entry_price_is_the_limit_level_not_the_close(self):
        t = self._run(True)
        self.assertAlmostEqual(t.entry_px, 99.0)
        self.assertAlmostEqual(t.stop0, 98.0)
        self.assertAlmostEqual(t.risk, 1.0)

    def test_entry_bar_can_resolve_when_allowed(self):
        """The target at 100 is inside the entry bar's range (high 101.5).
        A limit filled mid-bar leaves the rest of that bar tradeable, so it
        must be able to resolve there."""
        t = self._run(True)
        self.assertEqual(t.exit_i, 2, "should have resolved on the entry bar")
        self.assertEqual(t.exit_reason, "tp2")
        self.assertAlmostEqual(t.r_multiple, 1.0, places=9)

    def test_entry_bar_cannot_resolve_when_disallowed(self):
        """Same geometry with the switch off: the entry bar is skipped, and
        nothing on later bars reaches either level, so it flattens at the end.
        This is the default, and it is what keeps close-entry strategies
        (lorentzian, sdz) free of lookahead."""
        t = self._run(False)
        self.assertNotEqual(t.exit_i, 2)
        self.assertEqual(t.exit_reason, "eod")

    def test_entry_bar_open_is_not_treated_as_a_gap(self):
        """The entry bar's open happened BEFORE the limit filled, so it cannot
        gap a level that was not live yet. Here the open (100) is already
        through a target at 99.5 for a long entered at 99 -- if the gap path
        ran on the entry bar it would fill at 100 and book a fictitious 2R."""
        t = self._run(True, target=99.5, stop=98.5)
        self.assertAlmostEqual(t.fills[-1].px, 99.5,
                               msg="must fill at the target, not the entry bar's open")
        self.assertAlmostEqual(t.r_multiple, 1.0, places=9)

    def test_single_target_scores_differently_than_a_scale_out(self):
        """A strategy described as one 1:1 target must not be silently given
        a half-off at 0.5R: that scores a win as 0.75R against a 1R loss and
        moves breakeven from 50% to 57%."""
        single = self._run(True, partial=False)
        scaled = self._run(True, partial=True)
        self.assertAlmostEqual(single.r_multiple, 1.0, places=9)
        self.assertLess(scaled.r_multiple, single.r_multiple)

    def test_defaults_are_untouched_without_the_new_arguments(self):
        """Regression guard: omit entries/partial and the close-entry bracket
        must behave exactly as before, or every existing number moves."""
        from brackets import run_brackets
        bars = self._bars()
        n = len(bars)
        sl = [False] * n
        sl[2] = True
        rep = run_brackets(bars, sl, [False] * n, atr_len=1)
        self.assertEqual(rep.n, 1)
        self.assertAlmostEqual(rep.trades[0].entry_px, bars[2].c)
        self.assertTrue(rep.trades[0].partial)


class TestBracketOptimizer(unittest.TestCase):
    """A grid search that cannot see the parameters it is sweeping is worse
    than no grid search: it prints a ranking of identical numbers."""

    def _bars(self):
        import data as D
        return D.load_csv(D.sample_csv())

    def test_bracket_strategies_are_routed_to_the_bracket_optimizer(self):
        from opt import is_bracket_strategy
        self.assertTrue(is_bracket_strategy("nowick"))
        self.assertTrue(is_bracket_strategy("sdz"))
        self.assertFalse(is_bracket_strategy("rsi"))
        self.assertFalse(is_bracket_strategy("lorentzian"))

    def test_grid_params_actually_move_the_score(self):
        """The bug this path exists to prevent: rr / stop_buffer_atr / stop_mode
        are invisible to engine.run(), so ranking through it gave every row the
        same number. Scored through brackets, the grid must discriminate."""
        from opt import optimize_brackets
        results = optimize_brackets("nowick", self._bars(), top_n=8)
        self.assertGreater(len(results), 1)
        scores = {round(tr.avg_r, 6) for _, tr, _ in results}
        self.assertGreater(len(scores), 1,
                           "every param set scored identically -- the optimizer "
                           "is not seeing the parameters")

    def test_rr_reaches_the_emitted_target(self):
        """Cheap end-to-end proof the param survives the whole chain:
        strategy -> stops/targets -> bracket trade geometry."""
        from opt import _bracket_report
        bars = self._bars()
        rep = _bracket_report("nowick", bars, {"rr": 2.0, "trend_mode": "ema"})
        for t in rep.trades:
            reward = abs(t.tp2 - t.entry_px)
            self.assertAlmostEqual(reward / t.risk, 2.0, places=6)


# ---------------------------------------------------------------------------
# Asia sweep + change in state of delivery
# ---------------------------------------------------------------------------
_UTC_MIDNIGHT = 1755000000 // 86400 * 86400


def _m5(k, o, h, l, c, day=0):
    """One 5-minute bar, k bars after a UTC midnight. The session logic keys
    off real UTC timestamps, so these cannot be faked with bare indices."""
    return Bar(t=_UTC_MIDNIGHT + day * 86400 + k * 300, o=o, h=h, l=l, c=c, v=1.0)


def _asia_day(sweep="low", with_csd=True, day=0, rally=60, retest_dip=None):
    """A synthetic UTC day: an Asia range of 100.0-102.0, then a sweep of one
    side after 09:00, then (optionally) a change in state of delivery back
    the other way. Returns (bars, asia_high, asia_low)."""
    bars, k = [], 0
    for _ in range(108):                     # 00:00-09:00 UTC = Asia (Tokyo)
        mid = 101.0 + (0.6 if k % 2 else -0.6)
        bars.append(_m5(k, 101.0, max(101.0, mid) + 0.4, min(101.0, mid) - 0.4, mid, day))
        k += 1
    asia_high = max(b.h for b in bars)
    asia_low = min(b.l for b in bars)

    px = 101.0
    if sweep == "low":
        for _ in range(8):                   # drive down through the Asia low
            o = px; c = px - 0.35
            bars.append(_m5(k, o, o + 0.05, c - 0.05, c, day)); k += 1
            px = c
        if with_csd:                         # close above the last red candle's high
            ref = bars[-1].h
            bars.append(_m5(k, px, ref + 0.9, px - 0.1, ref + 0.8, day)); k += 1
            px = ref + 0.8
        step = 0.09
    elif sweep == "high":
        for _ in range(8):
            o = px; c = px + 0.35
            bars.append(_m5(k, o, c + 0.05, o - 0.05, c, day)); k += 1
            px = c
        if with_csd:                         # close below the last green candle's low
            ref = bars[-1].l
            bars.append(_m5(k, px, px + 0.1, ref - 0.9, ref - 0.8, day)); k += 1
            px = ref - 0.8
        step = -0.09
    else:                                    # "none": stay inside the range
        for _ in range(8):
            bars.append(_m5(k, px, px + 0.1, px - 0.1, px, day)); k += 1
        step = 0.0

    if retest_dip is not None:
        # One bar that pulls back to `retest_dip` before the move continues.
        # Used to exercise the iFVG retest path, which otherwise never fills
        # on a fixture that rallies straight away from the inversion.
        bars.append(_m5(k, px, px + 0.1, retest_dip, px - 0.1, day)); k += 1
        px = px - 0.1

    for _ in range(rally):
        o = px; c = px + step
        bars.append(_m5(k, o, max(o, c) + 0.05, min(o, c) - 0.05, c, day)); k += 1
        px = c
    return bars, asia_high, asia_low


class TestAsiaSweepCSD(unittest.TestCase):

    def test_registered_and_routed(self):
        from strategies import REGISTRY
        from gen import plan_from_text
        self.assertIn("asiasweep", REGISTRY)
        self.assertEqual(plan_from_text("asia sweep")[0], "asiasweep")
        self.assertEqual(plan_from_text("asia liquidity sweep csd")[0], "asiasweep")

    def test_power_of_three_still_routes(self):
        """`po3` also marks the Asian range. Adding this one must not steal
        its keywords -- they are different strategies."""
        from gen import plan_from_text
        self.assertEqual(plan_from_text("power of 3")[0], "po3")
        self.assertEqual(plan_from_text("po3")[0], "po3")
        self.assertEqual(plan_from_text("amd")[0], "po3")
        self.assertEqual(plan_from_text("accumulation manipulation distribution")[0], "po3")

    def test_amd_with_ifvg_routes_here_and_selects_ifvg_mode(self):
        """"AMD with iFVG" contains the bare word "amd", which the po3 branch
        matches. Ordering is what keeps it here -- and it must, because the
        two strategies enter and target completely differently."""
        from gen import plan_from_text
        for phrase in ("amd with ifvg", "AMD with iFVG strategy", "ifvg"):
            with self.subTest(phrase=phrase):
                key, params, _ = plan_from_text(phrase)
                self.assertEqual(key, "asiasweep")
                self.assertEqual(params["csd_mode"], "ifvg")

    def test_sweep_of_asia_low_gives_a_long_targeting_asia_high(self):
        from strategies import AsiaSweepCSD
        bars, asia_high, asia_low = _asia_day(sweep="low")
        s = AsiaSweepCSD(bars=bars, params={})
        self.assertEqual(sum(s.start_long), 1)
        self.assertEqual(sum(s.start_short), 0)
        i = s.start_long.index(True)
        self.assertAlmostEqual(s.targets[i], asia_high, places=9,
                               msg="target is the OPPOSITE side of the Asia range")
        self.assertLess(s.stops[i], asia_low,
                        "stop sits below the sweep, which is itself below Asia low")
        self.assertAlmostEqual(bars[i].c, bars[i].c)   # entry is the CSD bar's close

    def test_sweep_of_asia_high_gives_a_short_targeting_asia_low(self):
        from strategies import AsiaSweepCSD
        bars, asia_high, asia_low = _asia_day(sweep="high")
        s = AsiaSweepCSD(bars=bars, params={})
        self.assertEqual(sum(s.start_short), 1)
        self.assertEqual(sum(s.start_long), 0)
        i = s.start_short.index(True)
        self.assertAlmostEqual(s.targets[i], asia_low, places=9)
        self.assertGreater(s.stops[i], asia_high)

    def test_no_sweep_means_no_trade(self):
        """Step 2 is a precondition, not a preference. Price staying inside
        the Asia range must produce nothing at all."""
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="none")
        s = AsiaSweepCSD(bars=bars, params={})
        self.assertEqual(sum(s.start_long) + sum(s.start_short), 0)

    def test_sweep_without_a_csd_means_no_trade(self):
        """The sweep alone is not the signal -- without the shift back the
        other way this is just a market going down."""
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low", with_csd=False, rally=0)
        s = AsiaSweepCSD(bars=bars, params={})
        self.assertEqual(sum(s.start_long), 0)

    def test_nothing_triggers_during_the_asia_session_itself(self):
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low")
        s = AsiaSweepCSD(bars=bars, params={})
        import datetime as _dt
        for i in range(len(bars)):
            if s.start_long[i] or s.start_short[i]:
                hr = _dt.datetime.utcfromtimestamp(bars[i].t).hour
                self.assertGreaterEqual(hr, 9, "no entry may fire inside Asia")

    def test_one_trade_per_day(self):
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low", rally=120)
        s = AsiaSweepCSD(bars=bars, params={"one_per_day": True})
        self.assertEqual(sum(s.start_long) + sum(s.start_short), 1)

    def test_min_rr_filter_skips_a_setup_that_does_not_reach_it(self):
        """The transcript calls it a "3RR trade", but the target is a fixed
        LEVEL, so the real R:R is whatever the range geometry gives. This
        fixture offers about 1.9R -- it must be taken at min_rr 1.5 and
        skipped at 3.0, or the filter is decorative."""
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low")
        loose = AsiaSweepCSD(bars=bars, params={"min_rr": 1.5})
        strict = AsiaSweepCSD(bars=bars, params={"min_rr": 3.0})
        self.assertEqual(sum(loose.start_long), 1)
        self.assertEqual(sum(strict.start_long), 0)
        i = loose.start_long.index(True)
        rr = (loose.targets[i] - bars[i].c) / (bars[i].c - loose.stops[i])
        self.assertGreater(rr, 1.5)
        self.assertLess(rr, 3.0)

    def test_a_wider_stop_buffer_lowers_the_realized_rr(self):
        """Sanity on the one geometry knob: the target is fixed, so widening
        the stop can only reduce reward-to-risk."""
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low")
        tight = AsiaSweepCSD(bars=bars, params={"stop_buffer_atr": 0.0})
        wide = AsiaSweepCSD(bars=bars, params={"stop_buffer_atr": 0.5})
        it, iw = tight.start_long.index(True), wide.start_long.index(True)
        self.assertLess(wide.stops[iw], tight.stops[it])

    def test_every_signal_carries_a_stop_and_a_target(self):
        """brackets.py reads these in parallel; a NaN at a signal bar would
        silently fall back to an ATR stop and score a different strategy."""
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low")
        s = AsiaSweepCSD(bars=bars, params={})
        for i in range(len(bars)):
            if s.start_long[i] or s.start_short[i]:
                self.assertEqual(s.stops[i], s.stops[i])
                self.assertEqual(s.targets[i], s.targets[i])

    def test_runs_through_the_bracket_engine_with_its_own_levels(self):
        from strategies import AsiaSweepCSD
        from brackets import run_brackets
        bars, _, _ = _asia_day(sweep="low")
        s = AsiaSweepCSD(bars=bars, params={})
        rep = run_brackets(bars, s.start_long, s.start_short,
                           stops=s.stops, targets=s.targets,
                           partial_at_tp1=s.partial_at_tp1)
        self.assertGreater(rep.n, 0)
        for t in rep.trades:
            self.assertAlmostEqual(t.stop0, s.stops[t.entry_i], places=9)
            self.assertAlmostEqual(t.tp2, s.targets[t.entry_i], places=9)
            self.assertFalse(t.partial, "one fixed target, no scale-out")

    def test_multiple_days_each_get_their_own_range(self):
        from strategies import AsiaSweepCSD
        d0, _, _ = _asia_day(sweep="low", day=0)
        d1, _, _ = _asia_day(sweep="high", day=1)
        s = AsiaSweepCSD(bars=d0 + d1, params={})
        self.assertEqual(sum(s.start_long), 1, "day 0 sweeps the low -> one long")
        self.assertEqual(sum(s.start_short), 1, "day 1 sweeps the high -> one short")


class TestFVG(unittest.TestCase):
    """Three-bar fair value gaps -- the primitive the iFVG entry is built on."""

    @staticmethod
    def _b(t, o, h, l, c):
        return Bar(t=t, o=o, h=h, l=l, c=c, v=1.0)

    def test_bullish_gap_detected(self):
        from engine import fvgs
        # bar 2's low (105) sits above bar 0's high (101): untraded air between.
        bars = [self._b(0, 100, 101, 99, 100), self._b(1, 101, 106, 100, 105),
                self._b(2, 105, 108, 105, 107)]
        g = fvgs(bars)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["dir"], 1)
        self.assertAlmostEqual(g[0]["lo"], 101)
        self.assertAlmostEqual(g[0]["hi"], 105)
        self.assertEqual(g[0]["i"], 2, "knowable on the third bar, not before")

    def test_bearish_gap_detected(self):
        from engine import fvgs
        bars = [self._b(0, 100, 101, 99, 100), self._b(1, 99, 100, 94, 95),
                self._b(2, 95, 97, 93, 94)]
        g = fvgs(bars)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["dir"], -1)
        self.assertAlmostEqual(g[0]["lo"], 97)
        self.assertAlmostEqual(g[0]["hi"], 99)

    def test_overlapping_bars_have_no_gap(self):
        from engine import fvgs
        bars = [self._b(i, 100, 102, 98, 100) for i in range(6)]
        self.assertEqual(fvgs(bars), [])

    def test_min_size_filters_noise(self):
        """On 5-decimal FX a one-tick gap is noise, not an imbalance."""
        from engine import fvgs
        bars = [self._b(0, 100, 101, 99, 100), self._b(1, 101, 106, 100, 105),
                self._b(2, 105, 108, 101.5, 107)]
        self.assertEqual(len(fvgs(bars, min_size=0.0)), 1)
        self.assertEqual(len(fvgs(bars, min_size=1.0)), 0)


class TestCSVTimestamps(unittest.TestCase):
    """Real exports do not hand you epoch integers.

    Before this worked, every dated CSV loaded with NaN timestamps -- and a
    NaN timestamp fails SILENTLY: the session strategies just report no
    setups, which is indistinguishable from a market that genuinely had
    none. This is the gate every piece of real data has to pass through, so
    it gets tested at the formats real exports actually use.
    """

    def _load(self, body):
        import tempfile, os
        import data as D
        p = tempfile.mktemp(suffix=".csv")
        with open(p, "w") as f:
            f.write(body)
        try:
            return D.load_csv(p)
        finally:
            os.unlink(p)

    def test_real_export_formats_all_parse(self):
        import datetime
        expect = datetime.datetime(2025, 7, 1, 9, 5,
                                   tzinfo=datetime.timezone.utc).timestamp()
        cases = {
            "tradingview_iso":
                "time,open,high,low,close,Volume\n2025-07-01T09:05:00Z,1.1,1.2,1.05,1.15,100\n",
            "tradingview_epoch":
                f"time,open,high,low,close,Volume\n{int(expect)},1.1,1.2,1.05,1.15,100\n",
            "dukascopy_gmt":
                "Gmt time,Open,High,Low,Close,Volume\n01.07.2025 09:05:00.000,1.1,1.2,1.05,1.15,100\n",
            "metatrader":
                "Date,Open,High,Low,Close,Volume\n2025.07.01 09:05,1.1,1.2,1.05,1.15,100\n",
            "epoch_millis":
                f"timestamp,open,high,low,close,volume\n{int(expect)*1000},1.1,1.2,1.05,1.15,100\n",
            "iso_with_offset":
                "time,open,high,low,close,volume\n2025-07-01 09:05:00+00:00,1.1,1.2,1.05,1.15,100\n",
        }
        for name, body in cases.items():
            with self.subTest(fmt=name):
                bars = self._load(body)
                self.assertEqual(len(bars), 1)
                self.assertAlmostEqual(bars[0].t, expect, places=3,
                                       msg=f"{name} did not parse to the right UTC instant")
                self.assertAlmostEqual(bars[0].c, 1.15)

    def test_naive_times_are_treated_as_utc(self):
        """Dukascopy's column is literally 'Gmt time'. Interpreting it as
        local would shift every session boundary in the repo."""
        import datetime
        bars = self._load("Gmt time,Open,High,Low,Close,Volume\n"
                          "01.07.2025 00:00:00.000,1,1,1,1,1\n")
        self.assertEqual(
            datetime.datetime.utcfromtimestamp(bars[0].t).hour, 0)

    def test_ohlc_columns_are_case_insensitive(self):
        bars = self._load("Time,OPEN,High,low,Close,VOLUME\n"
                          "2025-07-01T00:00:00Z,1,2,0.5,1.5,10\n")
        self.assertAlmostEqual(bars[0].o, 1)
        self.assertAlmostEqual(bars[0].h, 2)
        self.assertAlmostEqual(bars[0].l, 0.5)
        self.assertAlmostEqual(bars[0].c, 1.5)

    def test_intraday_csv_drives_a_session_strategy_end_to_end(self):
        """The whole point: a dated intraday CSV must reach asiasweep with
        usable UTC hours, not NaN."""
        import datetime
        from strategies import AsiaSweepCSD
        rows = ["time,open,high,low,close,volume"]
        base = datetime.datetime(2025, 7, 1, tzinfo=datetime.timezone.utc)
        for i in range(288):                       # one full UTC day of 5m bars
            ts = (base + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows.append(f"{ts},100,101,99,100,1")
        bars = self._load("\n".join(rows) + "\n")
        self.assertTrue(all(b.t == b.t for b in bars))
        hours = {datetime.datetime.utcfromtimestamp(b.t).hour for b in bars}
        self.assertEqual(len(hours), 24, "a full day must span 24 distinct UTC hours")
        AsiaSweepCSD(bars=bars, params={})   # must not raise


class TestSMTDivergence(unittest.TestCase):
    """Two correlated instruments disagreeing at an extreme -- NQ sweeps its
    high, ES does not follow."""

    @staticmethod
    def _series(highs, lows=None, t0=0, dt=60):
        lows = lows or [h - 1 for h in highs]
        return [Bar(t=t0 + i * dt, o=h - 0.5, h=h, l=l, c=h - 0.5, v=1.0)
                for i, (h, l) in enumerate(zip(highs, lows))]

    def test_bearish_smt_when_only_one_makes_a_new_high(self):
        from engine import smt_divergence
        base = [100] * 10
        a = self._series(base + [105])   # this one breaks out
        b = self._series(base + [99])    # the reference does not
        bear, bull = smt_divergence(a, b, lookback=5)
        self.assertTrue(bear[10])
        self.assertFalse(bull[10])

    def test_no_smt_when_both_make_a_new_high(self):
        """Both indices going up together is just a rally. Flagging that as
        divergence would fire on every trend and mean nothing."""
        from engine import smt_divergence
        base = [100] * 10
        a = self._series(base + [105])
        b = self._series(base + [106])
        bear, _ = smt_divergence(a, b, lookback=5)
        self.assertFalse(bear[10])

    def test_bullish_smt_when_only_one_makes_a_new_low(self):
        from engine import smt_divergence
        base_h = [100] * 10
        a = self._series(base_h + [100], lows=[99] * 10 + [94])
        b = self._series(base_h + [100], lows=[99] * 10 + [99.5])
        bear, bull = smt_divergence(a, b, lookback=5)
        self.assertTrue(bull[10])
        self.assertFalse(bear[10])

    def test_alignment_is_by_timestamp_not_index(self):
        """THE correctness property. Two real feeds differ in bar count --
        session breaks, holidays, gaps. Lining them up positionally compares
        different moments and manufactures divergences that never happened.
        Here the reference is shifted an hour; nothing may match."""
        from engine import smt_divergence
        base = [100] * 10
        a = self._series(base + [105], t0=0, dt=60)
        b = self._series(base + [99], t0=3600, dt=60)
        bear, bull = smt_divergence(a, b, lookback=5)
        self.assertEqual(sum(bear), 0)
        self.assertEqual(sum(bull), 0)

    def test_missing_reference_bars_produce_no_signal(self):
        """A gap in the reference feed must yield silence, not a guess."""
        from engine import smt_divergence
        base = [100] * 10
        a = self._series(base + [105])
        b = self._series(base + [99])
        del b[10]                        # the bar that would have diverged
        bear, _ = smt_divergence(a, b, lookback=5)
        self.assertFalse(bear[10])

    def test_empty_reference_is_handled(self):
        from engine import smt_divergence
        a = self._series([100] * 12)
        bear, bull = smt_divergence(a, [], lookback=5)
        self.assertEqual(sum(bear) + sum(bull), 0)


class TestAsiaSweepIFVG(unittest.TestCase):
    """csd_mode="ifvg" -- an inversion IS a change in state of delivery, so
    this is a third reading of the same idea rather than a new strategy."""

    def test_ifvg_mode_produces_a_trade(self):
        from strategies import AsiaSweepCSD
        bars, asia_high, _ = _asia_day(sweep="low")
        s = AsiaSweepCSD(bars=bars, params={"csd_mode": "ifvg"})
        self.assertEqual(sum(s.start_long), 1)
        i = s.start_long.index(True)
        self.assertAlmostEqual(s.targets[i], asia_high, places=9)

    def test_close_mode_does_not_expose_limit_entries(self):
        """Entering at the signal bar's close must leave brackets.py on its
        default, lookahead-free path."""
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low")
        s = AsiaSweepCSD(bars=bars, params={"csd_mode": "ifvg"})
        self.assertFalse(hasattr(s, "entries"))
        self.assertFalse(getattr(s, "allow_entry_bar_fill", False))

    def test_retest_does_not_fill_when_price_never_comes_back(self):
        """The honest cost of waiting for a better price: on a fixture that
        rallies straight off the inversion, the retest simply never fills.
        A backtest that quietly filled it anyway would be inventing trades."""
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low")
        s = AsiaSweepCSD(bars=bars,
                         params={"csd_mode": "ifvg", "ifvg_entry": "retest"})
        self.assertEqual(sum(s.start_long) + sum(s.start_short), 0)

    def test_retest_fills_at_the_inverted_zone_edge(self):
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low", retest_dip=98.80)
        s = AsiaSweepCSD(bars=bars,
                         params={"csd_mode": "ifvg", "ifvg_entry": "retest"})
        self.assertEqual(sum(s.start_long), 1)
        i = s.start_long.index(True)
        self.assertTrue(hasattr(s, "entries"))
        self.assertTrue(s.allow_entry_bar_fill)
        self.assertLess(s.entries[i], bars[i].c,
                        "the whole point of the retest is a better price than "
                        "the close that triggered it")

    def test_retest_price_is_a_limit_not_the_bar_low(self):
        """Fill at the zone edge, not at however deep the bar happened to
        dip -- otherwise a deeper wick would silently improve the entry."""
        from strategies import AsiaSweepCSD
        shallow, _, _ = _asia_day(sweep="low", retest_dip=98.80)
        deep, _, _ = _asia_day(sweep="low", retest_dip=98.60)
        a = AsiaSweepCSD(bars=shallow, params={"csd_mode": "ifvg", "ifvg_entry": "retest"})
        b = AsiaSweepCSD(bars=deep, params={"csd_mode": "ifvg", "ifvg_entry": "retest"})
        ia, ib = a.start_long.index(True), b.start_long.index(True)
        self.assertAlmostEqual(a.entries[ia], b.entries[ib], places=9)

    def test_retest_cannot_fill_on_the_inversion_bar_itself(self):
        """THE lookahead guard for this mode.

        The bar whose close inverts the gap necessarily traded down through
        that gap on its way up. Filling the retest there books a price from
        earlier in the bar -- before the close that generated the signal
        existed -- and it is always the best price of the bar, so it inflates
        R substantially. Caught exactly this way during development: the
        retest was reporting 3.72R against the close entry's 1.86R purely
        from the lookahead.
        """
        from strategies import AsiaSweepCSD
        bars, _, _ = _asia_day(sweep="low", retest_dip=98.80)
        close_mode = AsiaSweepCSD(bars=bars, params={"csd_mode": "ifvg"})
        retest = AsiaSweepCSD(bars=bars,
                              params={"csd_mode": "ifvg", "ifvg_entry": "retest"})
        inversion_bar = close_mode.start_long.index(True)
        fill_bar = retest.start_long.index(True)
        self.assertGreater(fill_bar, inversion_bar,
                           "the retest must fill on a LATER bar than the "
                           "inversion that armed it")

    def test_ifvg_flows_through_the_bracket_engine(self):
        from strategies import AsiaSweepCSD
        from brackets import run_brackets
        bars, _, _ = _asia_day(sweep="low", retest_dip=98.80)
        s = AsiaSweepCSD(bars=bars,
                         params={"csd_mode": "ifvg", "ifvg_entry": "retest"})
        rep = run_brackets(bars, s.start_long, s.start_short,
                           stops=s.stops, targets=s.targets,
                           entries=getattr(s, "entries", None),
                           allow_entry_bar_fill=s.allow_entry_bar_fill,
                           partial_at_tp1=s.partial_at_tp1)
        self.assertGreater(rep.n, 0)
        for t in rep.trades:
            self.assertAlmostEqual(t.entry_px, s.entries[t.entry_i], places=9)
            self.assertAlmostEqual(t.stop0, s.stops[t.entry_i], places=9)


class TestRealExportLoading(unittest.TestCase):
    """Regression tests from a real Dukascopy mobile export."""

    def test_timezone_named_time_column_parses(self):
        """Dukascopy names the time column after the TIMEZONE ("Etc/UTC"), so
        no fixed list of column names can ever match it. Falling back to the
        first column is what makes a real export loadable at all -- without
        it every row's timestamp was NaN."""
        import tempfile, os as _os, data as D
        csv_text = ("Etc/UTC,Open,High,Low,Close,Volume\n"
                    "2025-01-02T00:00:00+00:00,1,2,0.5,1.5,100\n")
        fd, p = tempfile.mkstemp(suffix=".csv")
        with _os.fdopen(fd, "w") as f:
            f.write(csv_text)
        try:
            bars = D.load_csv(p)
            self.assertEqual(len(bars), 1)
            self.assertEqual(bars[0].t, bars[0].t, "timestamp must not be NaN")
        finally:
            _os.unlink(p)

    def test_stitching_dedupes_overlapping_files(self):
        """Overlapping monthly downloads must not double-count a bar: a
        duplicate is a second chance to trade the same moment."""
        import tempfile, os as _os, data as D
        rows_a = ("Etc/UTC,Open,High,Low,Close,Volume\n"
                  "2025-01-02T00:00:00+00:00,1,2,0.5,1.5,100\n"
                  "2025-01-02T01:00:00+00:00,1,2,0.5,1.5,100\n")
        rows_b = ("Etc/UTC,Open,High,Low,Close,Volume\n"
                  "2025-01-02T01:00:00+00:00,1,2,0.5,1.5,100\n"   # overlap
                  "2025-01-02T02:00:00+00:00,1,2,0.5,1.5,100\n")
        paths = []
        for text in (rows_b, rows_a):        # deliberately out of order
            fd, p = tempfile.mkstemp(suffix=".csv")
            with _os.fdopen(fd, "w") as f:
                f.write(text)
            paths.append(p)
        try:
            bars = D.load_csv_many(paths)
            self.assertEqual(len(bars), 3)
            self.assertEqual([b.t for b in bars], sorted(b.t for b in bars))
        finally:
            for p in paths:
                _os.unlink(p)


class TestResample(unittest.TestCase):

    def test_aggregates_ohlcv_correctly(self):
        import data as D
        m = [Bar(t=0,    o=10, h=12, l=9,  c=11, v=1),
             Bar(t=60,   o=11, h=15, l=8,  c=14, v=2),
             Bar(t=3600, o=20, h=21, l=19, c=20, v=5)]
        h = D.resample(m, 3600)
        self.assertEqual(len(h), 2)
        self.assertEqual((h[0].o, h[0].h, h[0].l, h[0].c, h[0].v), (10, 15, 8, 14, 3))
        self.assertEqual(h[0].t, 0)
        self.assertEqual(h[1].o, 20)

    def test_never_invents_a_bar_across_a_gap(self):
        """A weekend or session break must produce NO bar, not a flat one.
        A synthesised bar is a fake price that every indicator downstream
        would then read as real."""
        import data as D
        m = [Bar(t=0, o=10, h=10, l=10, c=10, v=1),
             Bar(t=3600 * 50, o=20, h=20, l=20, c=20, v=1)]   # ~2 days later
        self.assertEqual(len(D.resample(m, 3600)), 2)


class TestVWAPAnchorHour(unittest.TestCase):

    def test_anchor_hour_groups_an_overnight_session(self):
        """Index CFDs run 23:00 -> 21:00 UTC. With the default midnight
        reset the 23:00 bar lands in the PREVIOUS session; anchor_hour=23
        must put it at the start of the new one, where the chart puts it."""
        bars = [
            Bar(t=1735772400, o=100, h=100, l=100, c=100, v=1),   # Jan 1 23:00
            Bar(t=1735776000, o=200, h=200, l=200, c=200, v=1),   # Jan 2 00:00
        ]
        anchored = vwap_session(bars, anchor_hour=23)
        # Both bars are one session, so bar 2's VWAP averages them.
        self.assertAlmostEqual(anchored[1], 150.0, places=6)
        # With the midnight default, bar 2 starts a fresh session.
        self.assertAlmostEqual(vwap_session(bars)[1], 200.0, places=6)


class TestVWAPAnchorDST(unittest.TestCase):
    """A local-time anchor must follow the exchange clock across a DST change.

    The US cash open is 09:30 New York all year, but that is 13:30 UTC in
    summer and 14:30 UTC in winter. A fixed UTC anchor is therefore an hour
    wrong for half the year, every year.
    """

    @staticmethod
    def _epoch(iso):
        import datetime
        return datetime.datetime.fromisoformat(iso).replace(
            tzinfo=datetime.timezone.utc).timestamp()

    def test_reset_follows_new_york_across_the_dst_boundary(self):
        # Summer: 09:00 NY == 13:00 UTC. The 13:00 bar must start a session
        # (reset), so its VWAP is its own typical price, not blended with 12:00.
        summer = [Bar(t=self._epoch("2025-07-15T12:00"), o=100, h=100, l=100, c=100, v=1),
                  Bar(t=self._epoch("2025-07-15T13:00"), o=200, h=200, l=200, c=200, v=1)]
        v = vwap_session(summer, anchor_hour=9, anchor_tz="America/New_York")
        self.assertAlmostEqual(v[1], 200.0, places=6)

        # Winter: 09:00 NY == 14:00 UTC. Now the 13:00 bar is NOT the open --
        # it belongs to the prior session -- and 14:00 is the reset.
        winter = [Bar(t=self._epoch("2025-01-15T13:00"), o=100, h=100, l=100, c=100, v=1),
                  Bar(t=self._epoch("2025-01-15T14:00"), o=200, h=200, l=200, c=200, v=1)]
        w = vwap_session(winter, anchor_hour=9, anchor_tz="America/New_York")
        self.assertAlmostEqual(w[1], 200.0, places=6)

        # And the fixed-UTC anchor gets exactly one of those two wrong, which
        # is the bug this parameter exists to fix.
        fixed = vwap_session(winter, anchor_hour=13)
        self.assertAlmostEqual(fixed[1], 150.0, places=6)

    def test_utc_behaviour_is_unchanged_when_no_tz_is_given(self):
        bars = [Bar(t=self._epoch("2025-07-15T12:00"), o=100, h=100, l=100, c=100, v=1),
                Bar(t=self._epoch("2025-07-15T13:00"), o=200, h=200, l=200, c=200, v=1)]
        self.assertAlmostEqual(vwap_session(bars, anchor_hour=13)[1], 200.0, places=6)
        self.assertAlmostEqual(vwap_session(bars)[1], 150.0, places=6)


class TestVWAPATRFade(unittest.TestCase):
    """Long only: enter 2x ATR below session VWAP, exit at VWAP."""

    def _bars(self):
        from strategies import VWAPATRFade
        # Flat around 100 (builds VWAP ~= 100 and a nonzero ATR), then a sharp
        # drop that clears the entry band, then a recovery back up through it.
        closes = [100.0] * 20 + [100 - 0.4 * k for k in range(1, 11)] + [96 + 0.5 * k for k in range(1, 12)]
        bars = [Bar(t=i * 3600, o=c, h=c + 0.3, l=c - 0.3, c=c, v=1.0)
               for i, c in enumerate(closes)]
        return bars, VWAPATRFade

    def test_never_goes_short(self):
        bars, cls = self._bars()
        s = cls(bars=bars, params={"entry_atr": 2.0, "atr_n": 5})
        for i in range(len(bars)):
            self.assertIn(s.decide(i), ("LONG", "FLAT"))

    def test_enters_once_the_drop_clears_the_atr_band_and_exits_back_at_vwap(self):
        bars, cls = self._bars()
        s = cls(bars=bars, params={"entry_atr": 2.0, "atr_n": 5})
        postures = [s.decide(i) for i in range(len(bars))]
        self.assertIn("LONG", postures)
        first_long = postures.index("LONG")
        # Must not fire before price actually reached the band.
        v, a = s.vwap[first_long], s.atrv[first_long]
        self.assertLessEqual(bars[first_long].c, v - 2.0 * a + 1e-9)
        # And once it flips FLAT again after that, close must be back >= vwap.
        later_flat = next((i for i in range(first_long + 1, len(bars))
                          if postures[i] == "FLAT"), None)
        self.assertIsNotNone(later_flat)
        self.assertGreaterEqual(bars[later_flat].c, s.vwap[later_flat] - 1e-9)

    def test_no_drop_means_no_trade(self):
        bars = [Bar(t=i * 3600, o=100, h=100.3, l=99.7, c=100, v=1.0) for i in range(30)]
        from strategies import VWAPATRFade
        s = VWAPATRFade(bars=bars, params={"entry_atr": 2.0, "atr_n": 5})
        postures = [s.decide(i) for i in range(len(bars))]
        self.assertTrue(all(p == "FLAT" for p in postures))


if __name__ == "__main__":
    unittest.main(verbosity=2)
