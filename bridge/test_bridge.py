"""
test_bridge.py - tests for the webhook validator.

This is the component standing between a public URL and a live brokerage
account. Its job is to REFUSE things, so that is what gets tested: every
test below is a case that must be rejected, plus the few that must pass.
Run:  python bridge/test_bridge.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from webhook_server import Config, validate, to_queue_line, enqueue

SECRET = "a-long-enough-secret"


def cfg(**kw):
    opts = dict(secret=SECRET, symbols=["EURNZD", "NQ1!"],
                queue_path=tempfile.mktemp(), log_path=tempfile.mktemp(),
                live=True, max_lots=0.10)
    opts.update(kw)
    return Config(**opts)


def order(**kw):
    o = {"secret": SECRET, "id": "abc123", "action": "open", "symbol": "EURNZD",
         "side": "buy", "type": "limit", "price": 1.9600, "sl": 1.9550,
         "tp": 1.9700, "lots": 0.05}
    o.update(kw)
    return o


def risk_order(**kw):
    """Sized by money risked rather than an explicit lot count."""
    o = order(lots=0, risk=100.0)
    o.update(kw)
    return o


class TestAuth(unittest.TestCase):

    def test_valid_order_is_accepted(self):
        cmd, reason = validate(cfg(), order())
        self.assertIsNotNone(cmd, reason)
        self.assertEqual(cmd["symbol"], "EURNZD")
        self.assertEqual(cmd["side"], "buy")

    def test_wrong_secret_is_rejected(self):
        cmd, reason = validate(cfg(), order(secret="guess"))
        self.assertIsNone(cmd)
        self.assertIn("secret", reason)

    def test_missing_secret_is_rejected(self):
        payload = order()
        del payload["secret"]
        cmd, _ = validate(cfg(), payload)
        self.assertIsNone(cmd)

    def test_non_object_payload_is_rejected(self):
        cmd, _ = validate(cfg(), ["not", "a", "dict"])
        self.assertIsNone(cmd)


class TestSymbolAllowlist(unittest.TestCase):

    def test_symbol_outside_the_allowlist_is_rejected(self):
        cmd, reason = validate(cfg(), order(symbol="XAUUSD"))
        self.assertIsNone(cmd)
        self.assertIn("allowlist", reason)

    def test_symbol_matching_is_case_insensitive(self):
        cmd, _ = validate(cfg(), order(symbol="eurnzd"))
        self.assertIsNotNone(cmd)


class TestSizeLimits(unittest.TestCase):

    def test_lots_above_the_cap_are_rejected(self):
        """A typo in a Pine alert template must not become a large position."""
        cmd, reason = validate(cfg(max_lots=0.10), order(lots=5.0))
        self.assertIsNone(cmd)
        self.assertIn("cap", reason)

    def test_zero_or_negative_lots_rejected(self):
        for bad in (0, -1):
            with self.subTest(lots=bad):
                self.assertIsNone(validate(cfg(), order(lots=bad))[0])

    def test_non_numeric_fields_rejected(self):
        self.assertIsNone(validate(cfg(), order(price="abc"))[0])


class TestStopDirection(unittest.TestCase):
    """The most expensive typo available: a stop on the wrong side turns a
    bracket into an instant loss, or leaves the position unprotected."""

    def test_buy_stop_above_entry_is_rejected(self):
        cmd, reason = validate(cfg(), order(side="buy", price=1.96, sl=1.97))
        self.assertIsNone(cmd)
        self.assertIn("stop-loss", reason)

    def test_sell_stop_below_entry_is_rejected(self):
        cmd, reason = validate(cfg(), order(side="sell", price=1.96,
                                            sl=1.95, tp=1.94))
        self.assertIsNone(cmd)
        self.assertIn("stop-loss", reason)

    def test_buy_target_below_entry_is_rejected(self):
        cmd, reason = validate(cfg(), order(side="buy", price=1.96,
                                            sl=1.95, tp=1.94))
        self.assertIsNone(cmd)
        self.assertIn("target", reason)

    def test_correct_sell_bracket_is_accepted(self):
        cmd, reason = validate(cfg(), order(side="sell", price=1.96,
                                            sl=1.97, tp=1.94))
        self.assertIsNotNone(cmd, reason)


class TestDuplicates(unittest.TestCase):

    def test_same_id_twice_is_rejected(self):
        """TradingView re-fires alerts. Without this, a retry doubles the
        position -- silently, and at exactly the wrong moment."""
        c = cfg()
        cmd, _ = validate(c, order(id="dup-1"))
        self.assertIsNotNone(cmd)
        c.seen[cmd["id"]] = __import__("time").time()
        cmd2, reason = validate(c, order(id="dup-1"))
        self.assertIsNone(cmd2)
        self.assertIn("duplicate", reason)

    def test_missing_id_still_dedupes_within_the_minute(self):
        c = cfg()
        p = order()
        del p["id"]
        cmd, _ = validate(c, p)
        self.assertIsNotNone(cmd)
        c.seen[cmd["id"]] = __import__("time").time()
        p2 = order()
        del p2["id"]
        self.assertIsNone(validate(c, p2)[0])


class TestActions(unittest.TestCase):

    def test_unknown_action_is_rejected(self):
        self.assertIsNone(validate(cfg(), order(action="liquidate"))[0])

    def test_close_needs_no_side_or_price(self):
        cmd, reason = validate(cfg(), {"secret": SECRET, "id": "c1",
                                       "action": "close", "symbol": "EURNZD"})
        self.assertIsNotNone(cmd, reason)
        self.assertEqual(cmd["action"], "close")

    def test_unknown_order_type_is_rejected(self):
        self.assertIsNone(validate(cfg(), order(type="iceberg"))[0])


class TestRiskSizing(unittest.TestCase):
    """Money-risk sizing. The lots themselves are computed in MT5, which is
    the only place that knows tick value and account currency -- so what the
    server must get right is refusing anything that would make that
    calculation wrong or unbounded."""

    def test_risk_without_lots_is_accepted(self):
        cmd, reason = validate(cfg(), risk_order())
        self.assertIsNotNone(cmd, reason)
        self.assertEqual(cmd["risk"], 100.0)
        self.assertEqual(cmd["lots"], 0)

    def test_neither_lots_nor_risk_is_rejected(self):
        cmd, reason = validate(cfg(), order(lots=0))
        self.assertIsNone(cmd)
        self.assertIn("lots or risk", reason)

    def test_risk_without_a_stop_is_rejected(self):
        """Sizing off a stop distance is impossible with no stop. Accepting
        this would leave MT5 to invent a size."""
        cmd, reason = validate(cfg(), risk_order(sl=0))
        self.assertIsNone(cmd)
        self.assertIn("stop loss", reason)

    def test_risk_above_the_cap_is_rejected(self):
        cmd, reason = validate(cfg(max_risk=200.0), risk_order(risk=5000))
        self.assertIsNone(cmd)
        self.assertIn("cap", reason)

    def test_risk_travels_through_the_queue_line(self):
        cmd, _ = validate(cfg(), risk_order())
        fields = dict(p.split("=", 1) for p in to_queue_line(cmd).split("|"))
        self.assertEqual(float(fields["risk"]), 100.0)
        self.assertEqual(float(fields["lots"]), 0.0)


class TestQueueFormat(unittest.TestCase):

    def test_line_is_pipe_delimited_and_parseable(self):
        """The EA parses this with StringSplit. If the shape drifts, the EA
        silently reads empty fields and places a nonsense order."""
        cmd, _ = validate(cfg(), order())
        line = to_queue_line(cmd)
        fields = dict(p.split("=", 1) for p in line.split("|"))
        self.assertEqual(fields["symbol"], "EURNZD")
        self.assertEqual(fields["side"], "buy")
        self.assertEqual(fields["type"], "limit")
        self.assertEqual(float(fields["sl"]), 1.9550)
        for key in ("id", "action", "symbol", "side", "type", "price",
                    "sl", "tp", "lots", "risk", "comment"):
            self.assertIn(key, fields)

    def test_comment_cannot_break_the_delimiter(self):
        cmd, _ = validate(cfg(), order(comment="a|b=c"))
        line = to_queue_line(cmd)
        # The EA splits on '|' then '='; a comment carrying either would
        # shift every field after it. Assert the shape survives.
        fields = [p.split("=", 1)[0] for p in line.split("|")]
        self.assertEqual(fields[:5], ["id", "action", "symbol", "side", "type"])

    def test_enqueue_appends_a_line(self):
        c = cfg()
        cmd, _ = validate(c, order())
        enqueue(c, cmd)
        enqueue(c, cmd)
        with open(c.queue_path) as f:
            self.assertEqual(len(f.read().strip().splitlines()), 2)
        os.unlink(c.queue_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
