"""
test_health.py - stdlib unittest suite for the Telegram health chat.

Run:  python test_health.py
      python test_health.py -v

No network and no API key: the Telegram client is driven through its `transport`
hook, and every extraction test uses the keyword engine (`use_model=False`).
That is deliberate -- a test suite that needs credentials is a test suite nobody
runs.

The load-bearing tests here are the ones about *not lying*:
`test_unpriced_meal_is_not_a_zero_calorie_meal` and
`test_nudge_stays_quiet_when_macros_are_unknown`. A food log whose totals
silently treat "some chicken" as 0 kcal will report a deficit that never
happened, and a coach built on that will confidently tell you to eat more.
"""
from __future__ import annotations

import unittest
from datetime import timedelta

from health import coach, db, extract, ingest, report
from health.telegram import TelegramClient, parse_message


class FakeTelegram:
    """Stands in for the Bot API. Records what would have been sent."""

    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.sent = []

    def transport(self, method, params):
        if method == "getUpdates":
            batch, self.updates = self.updates, []
            return batch
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            return {"message_id": len(self.sent)}
        if method == "getMe":
            return {"id": 1, "username": "testbot"}
        raise AssertionError("unexpected method %s" % method)

    def client(self):
        return TelegramClient("test-token", transport=self.transport)


def update(text=None, *, message_id=1, update_id=100, chat_id=99, user_id=42, voice=False):
    msg = {"message_id": message_id, "chat": {"id": chat_id},
           "from": {"id": user_id, "first_name": "Stefan"}}
    if voice:
        msg["voice"] = {"file_id": "vox", "duration": 7}
    else:
        msg["text"] = text
    return {"update_id": update_id, "message": msg}


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.tg_fake = FakeTelegram()
        self.tg = self.tg_fake.client()

    def tearDown(self):
        self.conn.close()

    def user(self):
        return db.upsert_user(self.conn, 42, 99, "Stefan")


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

class TestStorage(HealthTest):
    def test_init_is_idempotent(self):
        db.init(self.conn)
        db.init(self.conn)
        self.assertEqual(db.counts(self.conn)["messages"], 0)

    def test_duplicate_telegram_message_is_dropped(self):
        u = self.user()
        first = db.add_message(self.conn, chat_id=99, user_id=u["id"],
                               telegram_message_id=7, text="ate rice")
        second = db.add_message(self.conn, chat_id=99, user_id=u["id"],
                                telegram_message_id=7, text="ate rice")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(db.counts(self.conn)["messages"], 1)

    def test_local_message_ids_do_not_collide(self):
        """Messages injected from the CLI have no Telegram id. SQLite treats
        NULLs as distinct in a UNIQUE index, which is what we want here."""
        u = self.user()
        a = db.add_message(self.conn, chat_id=0, user_id=u["id"], text="one")
        b = db.add_message(self.conn, chat_id=0, user_id=u["id"], text="two")
        self.assertNotEqual(a, b)

    def test_state_round_trip(self):
        db.set_state(self.conn, "telegram_offset", 12)
        self.assertEqual(db.get_state(self.conn, "telegram_offset"), "12")
        db.set_state(self.conn, "telegram_offset", 13)
        self.assertEqual(db.get_state(self.conn, "telegram_offset"), "13")


# ---------------------------------------------------------------------------
# fanning a payload out into tables
# ---------------------------------------------------------------------------

class TestWriteEntries(HealthTest):
    def test_meal_and_workout_land_in_their_own_tables(self):
        u = self.user()
        mid = db.add_message(self.conn, chat_id=99, user_id=u["id"], text="x")
        counts = db.write_entries(self.conn, u["id"], mid, {
            "meals": [{"description": "tandoori chicken", "calories": 620, "protein_g": 55,
                       "items": [{"name": "chicken thigh", "quantity": 300, "unit": "g"}]}],
            "workouts": [{"focus": "legs", "duration_min": 25,
                          "sets": [{"exercise": "squat", "set_no": 3, "reps": 8,
                                    "weight_kg": 100}]}],
        })
        self.assertEqual(counts, {"meals": 1, "workouts": 1})
        table = db.counts(self.conn)
        self.assertEqual(table["meal_items"], 1)
        self.assertEqual(table["exercise_sets"], 1)
        row = self.conn.execute("SELECT * FROM exercise_sets").fetchone()
        self.assertEqual(row["exercise"], "squat")
        self.assertEqual(row["weight_kg"], 100)

    def test_missing_when_falls_back_to_message_time(self):
        u = self.user()
        received = "2026-08-01T09:00:00Z"
        db.write_entries(self.conn, u["id"], None, {"meals": [{"description": "oats"}]},
                         default_ts=received)
        self.assertEqual(
            self.conn.execute("SELECT eaten_at FROM meals").fetchone()[0], received)

    def test_explicit_when_is_honoured_and_normalised(self):
        u = self.user()
        db.write_entries(self.conn, u["id"], None,
                         {"meals": [{"description": "oats", "when": "2026-07-04 08:30:00"}]},
                         default_ts="2026-08-01T09:00:00Z")
        self.assertEqual(
            self.conn.execute("SELECT eaten_at FROM meals").fetchone()[0],
            "2026-07-04T08:30:00Z")

    def test_unparseable_when_does_not_lose_the_row(self):
        u = self.user()
        db.write_entries(self.conn, u["id"], None,
                         {"meals": [{"description": "oats", "when": "yesterday-ish"}]},
                         default_ts="2026-08-01T09:00:00Z")
        self.assertEqual(
            self.conn.execute("SELECT eaten_at FROM meals").fetchone()[0],
            "2026-08-01T09:00:00Z")

    def test_unknown_keys_are_ignored(self):
        u = self.user()
        counts = db.write_entries(self.conn, u["id"], None,
                                  {"meals": [], "moon_phase": [{"phase": "waxing"}]})
        self.assertEqual(counts, {})

    def test_local_day_bucketing_crosses_utc_midnight(self):
        """A 22:30 Vienna dinner is 20:30 UTC the same day in summer, but a
        23:30 one is 21:30 UTC -- and in winter the UTC day flips. Bucketing on
        the raw UTC string would file those under the wrong day."""
        self.assertEqual(db.local_day("2026-01-15T23:30:00Z", "Europe/Vienna"),
                         "2026-01-16")
        self.assertEqual(db.local_day("2026-01-15T22:30:00Z", "Europe/Vienna"),
                         "2026-01-15")


# ---------------------------------------------------------------------------
# the keyword engine
# ---------------------------------------------------------------------------

class TestHeuristic(unittest.TestCase):
    def test_food_and_workout_in_one_message(self):
        p = extract.heuristic_payload(
            "oh I ate some tandoori chicken dude, and had a very short legs workout")
        self.assertEqual(len(p["meals"]), 1)
        self.assertEqual(len(p["workouts"]), 1)
        self.assertEqual(p["workouts"][0]["focus"], "legs")
        self.assertEqual(p["workouts"][0]["duration_min"], 20.0)

    def test_no_macros_are_invented(self):
        p = extract.heuristic_payload("ate some chicken")
        self.assertIsNone(p["meals"][0]["calories"])
        self.assertIsNone(p["meals"][0]["protein_g"])

    def test_sets_are_parsed(self):
        p = extract.heuristic_payload("gym: squats 3x8, bench 5x5")
        exercises = {s["exercise"] for s in p["workouts"][0]["sets"]}
        self.assertIn("squats", exercises)
        self.assertIn("bench", exercises)

    def test_weight_and_sleep(self):
        p = extract.heuristic_payload("weighed 82.4 kg this morning, slept 6 hours, rough night")
        self.assertEqual(p["body_metrics"][0]["weight_kg"], 82.4)
        self.assertEqual(p["sleep"][0]["hours"], 6.0)
        self.assertEqual(p["sleep"][0]["quality"], "poor")

    def test_lifted_weight_is_not_bodyweight(self):
        """'squats 3x8 at 100 kg' must not become a body_metrics row."""
        p = extract.heuristic_payload("squats 3x8 at 100 kg")
        self.assertEqual(p["body_metrics"], [])

    def test_hydration_in_litres(self):
        p = extract.heuristic_payload("drank 2 litres of water today")
        self.assertEqual(p["hydration"][0]["ml"], 2000.0)

    def test_hints_match_whole_words_only(self):
        """'creatine' contains 'ate'. Substring matching turned a supplement
        into a phantom meal row with no calories, which then poisoned the
        day's totals as an unpriced meal."""
        p = extract.heuristic_payload("drank 2 litres of water and took 5g creatine")
        self.assertEqual(p["meals"], [])
        self.assertEqual(p["hydration"][0]["ml"], 2000.0)

    def test_unrelated_text_becomes_a_note_not_a_meal(self):
        p = extract.heuristic_payload("reminder to call the landlord")
        self.assertEqual(p["meals"], [])
        self.assertEqual(len(p["notes"]), 1)


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

class TestIngest(HealthTest):
    def test_message_is_stored_and_confirmed(self):
        self.tg_fake.updates = [update("ate tandoori chicken")]
        results = ingest.poll_once(self.conn, self.tg, timeout=0)
        self.assertEqual(results[0]["status"], "pending")
        self.assertEqual(db.counts(self.conn)["messages"], 1)
        self.assertEqual(len(self.tg_fake.sent), 1)
        self.assertIn("saved", self.tg_fake.sent[0][1].lower())

    def test_offset_advances_past_handled_updates(self):
        self.tg_fake.updates = [update("one", message_id=1, update_id=500)]
        ingest.poll_once(self.conn, self.tg, timeout=0)
        self.assertEqual(db.get_state(self.conn, ingest.OFFSET_KEY), "501")

    def test_replayed_update_does_not_double_log(self):
        u = update("ate rice", message_id=3, update_id=700)
        self.tg_fake.updates = [u]
        ingest.poll_once(self.conn, self.tg, timeout=0)
        self.tg_fake.updates = [u]
        results = ingest.poll_once(self.conn, self.tg, timeout=0)
        self.assertIn("duplicate", results[0])
        self.assertEqual(db.counts(self.conn)["messages"], 1)

    def test_unknown_chat_is_dropped_silently(self):
        self.tg_fake.updates = [update("hello", chat_id=1234, user_id=1234)]
        results = ingest.poll_once(self.conn, self.tg, timeout=0, allowed={99})
        self.assertIn("skipped", results[0])
        self.assertEqual(db.counts(self.conn)["messages"], 0)
        self.assertEqual(self.tg_fake.sent, [])

    def test_voice_without_transcriber_is_kept_not_guessed(self):
        self.tg_fake.updates = [update(voice=True)]
        results = ingest.poll_once(self.conn, self.tg, timeout=0)
        self.assertEqual(results[0]["status"], "needs_transcription")
        self.assertEqual(db.counts(self.conn)["messages"], 1)
        self.assertEqual(db.pending_messages(self.conn), [])
        self.assertIn("transcriber", self.tg_fake.sent[0][1])

    def test_commands_are_answered_and_never_queued(self):
        self.tg_fake.updates = [update("/help")]
        results = ingest.poll_once(self.conn, self.tg, timeout=0)
        self.assertEqual(results[0]["command"], "/help")
        self.assertEqual(db.pending_messages(self.conn), [])
        self.assertIn("Commands", self.tg_fake.sent[0][1])

    def test_goal_command_sets_a_target(self):
        self.tg_fake.updates = [update("/goal protein_g 180")]
        ingest.poll_once(self.conn, self.tg, timeout=0)
        u = db.list_users(self.conn)[0]
        self.assertEqual(db.goals(self.conn, u["id"]), {"protein_g": 180.0})

    def test_unsupported_update_still_advances_the_offset(self):
        """A sticker must not wedge the queue at the same update forever."""
        self.tg_fake.updates = [{"update_id": 900, "message": {
            "message_id": 4, "chat": {"id": 99}, "sticker": {"file_id": "s"}}}]
        ingest.poll_once(self.conn, self.tg, timeout=0)
        self.assertEqual(db.get_state(self.conn, ingest.OFFSET_KEY), "901")

    def test_parse_message_ignores_non_messages(self):
        self.assertIsNone(parse_message({"update_id": 1, "edited_message": {}}))


# ---------------------------------------------------------------------------
# extraction pipeline
# ---------------------------------------------------------------------------

class TestProcessing(HealthTest):
    def test_pending_message_becomes_rows_and_is_marked_processed(self):
        self.tg_fake.updates = [update("ate chicken and rice, 30 min legs session")]
        ingest.poll_once(self.conn, self.tg, timeout=0)
        results = extract.process_pending(self.conn, use_model=False)
        self.assertEqual(results[0]["status"], "processed")
        self.assertEqual(db.pending_messages(self.conn), [])
        counts = db.counts(self.conn)
        self.assertEqual(counts["meals"], 1)
        self.assertEqual(counts["workouts"], 1)
        self.assertEqual(counts["extraction_runs"], 1)

    def test_extraction_run_records_the_engine_and_payload(self):
        u = self.user()
        mid = db.add_message(self.conn, chat_id=99, user_id=u["id"], text="ate oats")
        msg = dict(self.conn.execute("SELECT * FROM messages WHERE id = ?",
                                     (mid,)).fetchone())
        extract.process_message(self.conn, msg, use_model=False)
        run = self.conn.execute("SELECT * FROM extraction_runs").fetchone()
        self.assertEqual(run["engine"], "heuristic")
        self.assertIn("oats", run["raw_json"])

    def test_dry_run_writes_nothing(self):
        u = self.user()
        mid = db.add_message(self.conn, chat_id=99, user_id=u["id"], text="ate oats")
        msg = dict(self.conn.execute("SELECT * FROM messages WHERE id = ?",
                                     (mid,)).fetchone())
        extract.process_message(self.conn, msg, use_model=False, dry_run=True)
        self.assertEqual(db.counts(self.conn)["meals"], 0)
        self.assertEqual(len(db.pending_messages(self.conn)), 1)

    def test_model_failure_falls_back_instead_of_losing_the_message(self):
        def boom(*a, **kw):
            raise RuntimeError("no api key")

        original = extract.claude_payload
        extract.claude_payload = boom
        try:
            payload, engine, error = extract.extract("ate oats", db.now_iso())
        finally:
            extract.claude_payload = original
        self.assertEqual(engine, "heuristic")
        self.assertEqual(error, "no api key")
        self.assertEqual(len(payload["meals"]), 1)

    def test_empty_message_body_is_not_processed(self):
        u = self.user()
        mid = db.add_message(self.conn, chat_id=99, user_id=u["id"], kind="voice",
                             text=None, file_id="vox")
        msg = dict(self.conn.execute("SELECT * FROM messages WHERE id = ?",
                                     (mid,)).fetchone())
        result = extract.process_message(self.conn, msg, use_model=False)
        self.assertEqual(result["status"], "needs_transcription")
        self.assertEqual(db.counts(self.conn)["notes"], 0)


# ---------------------------------------------------------------------------
# not lying about the numbers
# ---------------------------------------------------------------------------

class TestHonestReporting(HealthTest):
    def test_unpriced_meal_is_not_a_zero_calorie_meal(self):
        u = self.user()
        db.write_entries(self.conn, u["id"], None,
                         {"meals": [{"description": "some chicken", "calories": None}]})
        day = db.daily_totals(self.conn, u["id"], days=1, tz_name="UTC")[-1]
        self.assertEqual(day["meals"], 1)
        self.assertEqual(day["meals_missing_macros"], 1)
        text = report.day_report(self.conn, u)
        self.assertIn("no calorie estimate", text)

    def test_week_report_counts_unlogged_days(self):
        u = self.user()
        db.write_entries(self.conn, u["id"], None,
                         {"meals": [{"description": "oats", "calories": 400}]})
        text = report.week_report(self.conn, u, days=7)
        self.assertIn("Anything logged on 1 of 7 days.", text)
        self.assertIn("describe your logging, not your eating", text)

    def test_nudge_stays_quiet_when_macros_are_unknown(self):
        """Without calorie data there is no protein shortfall to report -- only
        a logging gap. Claiming otherwise would be a number pulled from nothing."""
        u = self.user()
        db.set_goal(self.conn, u["id"], "protein_g", 180)
        db.write_entries(self.conn, u["id"], None,
                         {"meals": [{"description": "some chicken", "calories": None}]})
        stat = report.stats(self.conn, u, days=7)
        reasons = coach.nudge_reasons(stat)
        self.assertFalse(any("Protein on logged days" in r for r in reasons))
        self.assertTrue(any("calorie estimate" in r for r in reasons))

    def test_protein_shortfall_is_reported_when_the_data_supports_it(self):
        u = self.user()
        db.set_goal(self.conn, u["id"], "protein_g", 180)
        db.write_entries(self.conn, u["id"], None,
                         {"meals": [{"description": "oats", "calories": 400,
                                     "protein_g": 20}]})
        reasons = coach.nudge_reasons(report.stats(self.conn, u, days=7))
        self.assertTrue(any("Protein on logged days" in r for r in reasons))


# ---------------------------------------------------------------------------
# coach
# ---------------------------------------------------------------------------

class TestCoach(HealthTest):
    def test_nudge_says_nothing_when_there_is_nothing_to_say(self):
        u = self.user()
        # A full week of training and food, no targets missed.
        for offset in range(7):
            ts = db.iso(db.utc_now() - timedelta(days=offset, hours=2))
            db.write_entries(self.conn, u["id"], None, {
                "meals": [{"description": "chicken and rice", "calories": 800,
                           "protein_g": 60, "when": ts}],
                "workouts": [{"focus": "full body", "duration_min": 45, "when": ts}],
            })
        results = coach.run(self.conn, kind="nudge", use_model=False, dry_run=True)
        self.assertEqual(results[0]["skipped"], "nothing worth saying")

    def test_nudge_fires_on_a_real_training_gap(self):
        u = self.user()
        old = db.iso(db.utc_now() - timedelta(days=9))
        db.write_entries(self.conn, u["id"], None,
                         {"workouts": [{"focus": "legs", "duration_min": 40, "when": old}]})
        results = coach.run(self.conn, kind="nudge", use_model=False, dry_run=True)
        self.assertIn("No training logged in the last 4 days.", results[0]["reasons"])

    def test_daily_is_sent_once_per_day(self):
        self.user()
        first = coach.run(self.conn, kind="daily", use_model=False)
        second = coach.run(self.conn, kind="daily", use_model=False)
        self.assertNotIn("skipped", first[0])
        self.assertEqual(second[0]["skipped"], "already sent today")

    def test_force_overrides_the_daily_limit(self):
        self.user()
        coach.run(self.conn, kind="daily", use_model=False)
        again = coach.run(self.conn, kind="daily", use_model=False, force=True)
        self.assertNotIn("skipped", again[0])

    def test_sent_messages_are_logged_for_audit(self):
        u = self.user()
        coach.run(self.conn, kind="daily", use_model=False)
        row = db.last_coach_message(self.conn, u["id"], "daily")
        self.assertIsNotNone(row)
        self.assertIn("Check-in", row["body"])


if __name__ == "__main__":
    unittest.main()
