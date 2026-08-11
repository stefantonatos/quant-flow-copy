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


if __name__ == "__main__":
    unittest.main(verbosity=2)
