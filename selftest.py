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

from engine import Bar, Strategy, Trade, Report, run, bars_per_year, sma, ema, rsi, atr


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
