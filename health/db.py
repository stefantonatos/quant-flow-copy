"""
health/db.py - SQLite access layer for the health chat.

Everything that touches the database lives here. The rest of the package hands
this module plain dicts and gets plain dicts back, which keeps the Telegram code
and the model code free of SQL and makes both testable against an in-memory db.

Timestamps are ISO-8601 UTC with a 'Z' suffix, everywhere, always. Local time is
a display concern; see `local_day()`.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from . import config

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "schema.sql")

# Tables the extractor is allowed to write to, in the order they are applied.
# `meals` and `workouts` come first because their child rows reference them.
ENTRY_TABLES = (
    "meals",
    "workouts",
    "body_metrics",
    "sleep",
    "hydration",
    "supplements",
    "symptoms",
    "notes",
)


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Format a datetime as the one string shape this database stores."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(utc_now())


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse the timestamp shapes a model might plausibly emit. Returns None on
    anything unparseable rather than raising -- a bad timestamp should cost you
    a fallback to 'when you sent the message', not the whole row."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _zone(tz_name: Optional[str] = None):
    """Return a tzinfo for `tz_name`, falling back to UTC. zoneinfo needs the
    system tz database, which some minimal containers ship without."""
    name = tz_name or config.timezone_name()
    if name.upper() == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def local_day(ts: str, tz_name: Optional[str] = None) -> str:
    """The calendar day an ISO timestamp falls on in the user's timezone.

    Matters more than it looks: a 22:30 dinner in Vienna is the next UTC day in
    winter, so bucketing by `substr(eaten_at, 1, 10)` would file it under
    tomorrow's calories."""
    dt = parse_iso(ts)
    if dt is None:
        return ""
    return dt.astimezone(_zone(tz_name)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------

def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open (and if needed create) the database, with the schema applied."""
    target = path or config.db_path()
    if target != ":memory:":
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init(conn)
    return conn


def init(conn: sqlite3.Connection) -> None:
    """Apply schema.sql. Safe to run against an existing database."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.commit()


def _rows(cur) -> List[Dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# state (getUpdates offset, etc.)
# ---------------------------------------------------------------------------

def get_state(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# users and goals
# ---------------------------------------------------------------------------

def upsert_user(conn: sqlite3.Connection, telegram_id: int, chat_id: Optional[int] = None,
                name: Optional[str] = None, tz: Optional[str] = None) -> Dict[str, Any]:
    """Find the user by Telegram id, creating them on first contact."""
    row = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO users(telegram_id, chat_id, name, tz, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (telegram_id, chat_id, name, tz or config.timezone_name(), now_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    elif (chat_id and row["chat_id"] != chat_id) or (name and row["name"] != name):
        conn.execute(
            "UPDATE users SET chat_id = COALESCE(?, chat_id), name = COALESCE(?, name) "
            "WHERE id = ?",
            (chat_id, name, row["id"]),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    return dict(row)


def list_users(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return _rows(conn.execute("SELECT * FROM users ORDER BY id"))


def set_goal(conn: sqlite3.Connection, user_id: int, kind: str, target: float,
             unit: Optional[str] = None) -> None:
    conn.execute(
        "INSERT INTO goals(user_id, kind, target, unit, active, created_at) "
        "VALUES(?, ?, ?, ?, 1, ?) "
        "ON CONFLICT(user_id, kind) DO UPDATE SET target = excluded.target, "
        "unit = excluded.unit, active = 1",
        (user_id, kind, float(target), unit, now_iso()),
    )
    conn.commit()


def goals(conn: sqlite3.Connection, user_id: int) -> Dict[str, float]:
    cur = conn.execute(
        "SELECT kind, target FROM goals WHERE user_id = ? AND active = 1", (user_id,)
    )
    return {r["kind"]: r["target"] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# the inbox
# ---------------------------------------------------------------------------

def add_message(conn: sqlite3.Connection, *, chat_id: int, user_id: Optional[int] = None,
                telegram_message_id: Optional[int] = None, kind: str = "text",
                text: Optional[str] = None, transcript: Optional[str] = None,
                file_id: Optional[str] = None, file_path: Optional[str] = None,
                duration_s: Optional[int] = None, status: str = "pending",
                received_at: Optional[str] = None) -> Optional[int]:
    """Insert an inbound message. Returns the new row id, or None if this
    Telegram message was already stored.

    The None return is the idempotency guarantee: Telegram will happily redeliver
    an update if the offset acknowledgement is lost, and a duplicate here would
    mean logging the same dinner twice."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO messages("
        " user_id, chat_id, telegram_message_id, kind, text, transcript,"
        " file_id, file_path, duration_s, status, received_at)"
        " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, chat_id, telegram_message_id, kind, text, transcript,
         file_id, file_path, duration_s, status, received_at or now_iso()),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    return int(cur.lastrowid)


def pending_messages(conn: sqlite3.Connection, limit: int = 50) -> List[Dict[str, Any]]:
    return _rows(conn.execute(
        "SELECT * FROM messages WHERE status = 'pending' ORDER BY id LIMIT ?", (limit,)
    ))


def set_message_status(conn: sqlite3.Connection, message_id: int, status: str,
                       error: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE messages SET status = ?, error = ?, processed_at = ? WHERE id = ?",
        (status, error, now_iso() if status in ("processed", "failed") else None, message_id),
    )
    conn.commit()


def set_transcript(conn: sqlite3.Connection, message_id: int, transcript: str,
                   status: str = "pending") -> None:
    conn.execute(
        "UPDATE messages SET transcript = ?, status = ? WHERE id = ?",
        (transcript, status, message_id),
    )
    conn.commit()


def message_body(msg: Dict[str, Any]) -> str:
    """The text to interpret: what was typed, or what was transcribed."""
    return (msg.get("text") or msg.get("transcript") or "").strip()


def log_extraction(conn: sqlite3.Connection, message_id: int, engine: str, ok: bool,
                   raw_json: Optional[str] = None, error: Optional[str] = None) -> None:
    conn.execute(
        "INSERT INTO extraction_runs(message_id, engine, ok, raw_json, error, created_at)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        (message_id, engine, 1 if ok else 0, raw_json, error, now_iso()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# writing interpreted rows
# ---------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _when(entry: Dict[str, Any], default_ts: str) -> str:
    return iso(parse_iso(entry.get("when")) or parse_iso(default_ts) or utc_now())


def write_entries(conn: sqlite3.Connection, user_id: int, message_id: Optional[int],
                  payload: Dict[str, Any], default_ts: Optional[str] = None) -> Dict[str, int]:
    """Fan a extraction payload out into the typed tables.

    `payload` is the shape defined in extract.EXTRACTION_SCHEMA: one key per
    table, each holding a list of entries. Unknown keys are ignored so a model
    that invents a table cannot break the write. Returns a per-table count of
    rows written, which is what the confirmation message is built from."""
    default_ts = default_ts or now_iso()
    created = now_iso()
    counts: Dict[str, int] = {}

    def bump(table: str) -> None:
        counts[table] = counts.get(table, 0) + 1

    for meal in payload.get("meals") or []:
        cur = conn.execute(
            "INSERT INTO meals(user_id, message_id, eaten_at, meal_type, description,"
            " calories, protein_g, carbs_g, fat_g, confidence, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, message_id, _when(meal, default_ts), meal.get("meal_type"),
             meal.get("description"), _num(meal.get("calories")), _num(meal.get("protein_g")),
             _num(meal.get("carbs_g")), _num(meal.get("fat_g")), _num(meal.get("confidence")),
             created),
        )
        meal_id = int(cur.lastrowid)
        bump("meals")
        for item in meal.get("items") or []:
            if not item.get("name"):
                continue
            conn.execute(
                "INSERT INTO meal_items(meal_id, name, quantity, unit, calories,"
                " protein_g, carbs_g, fat_g) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (meal_id, item.get("name"), _num(item.get("quantity")), item.get("unit"),
                 _num(item.get("calories")), _num(item.get("protein_g")),
                 _num(item.get("carbs_g")), _num(item.get("fat_g"))),
            )

    for workout in payload.get("workouts") or []:
        cur = conn.execute(
            "INSERT INTO workouts(user_id, message_id, started_at, kind, focus,"
            " duration_min, intensity, perceived_effort, calories_est, notes,"
            " confidence, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, message_id, _when(workout, default_ts), workout.get("kind"),
             workout.get("focus"), _num(workout.get("duration_min")), workout.get("intensity"),
             _num(workout.get("perceived_effort")), _num(workout.get("calories_est")),
             workout.get("notes"), _num(workout.get("confidence")), created),
        )
        workout_id = int(cur.lastrowid)
        bump("workouts")
        for s in workout.get("sets") or []:
            if not s.get("exercise"):
                continue
            conn.execute(
                "INSERT INTO exercise_sets(workout_id, exercise, set_no, reps, weight_kg,"
                " rpe, distance_km, duration_s) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (workout_id, s.get("exercise"), _num(s.get("set_no")), _num(s.get("reps")),
                 _num(s.get("weight_kg")), _num(s.get("rpe")), _num(s.get("distance_km")),
                 _num(s.get("duration_s"))),
            )

    for m in payload.get("body_metrics") or []:
        conn.execute(
            "INSERT INTO body_metrics(user_id, message_id, measured_at, weight_kg,"
            " body_fat_pct, waist_cm, resting_hr, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, message_id, _when(m, default_ts), _num(m.get("weight_kg")),
             _num(m.get("body_fat_pct")), _num(m.get("waist_cm")), _num(m.get("resting_hr")),
             created),
        )
        bump("body_metrics")

    for s in payload.get("sleep") or []:
        conn.execute(
            "INSERT INTO sleep_logs(user_id, message_id, slept_at, hours, quality, notes,"
            " created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (user_id, message_id, _when(s, default_ts), _num(s.get("hours")),
             s.get("quality"), s.get("notes"), created),
        )
        bump("sleep")

    for h in payload.get("hydration") or []:
        conn.execute(
            "INSERT INTO hydration(user_id, message_id, drank_at, ml, drink, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (user_id, message_id, _when(h, default_ts), _num(h.get("ml")), h.get("drink"),
             created),
        )
        bump("hydration")

    for s in payload.get("supplements") or []:
        if not s.get("name"):
            continue
        conn.execute(
            "INSERT INTO supplements(user_id, message_id, taken_at, name, dose, unit,"
            " created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (user_id, message_id, _when(s, default_ts), s.get("name"), _num(s.get("dose")),
             s.get("unit"), created),
        )
        bump("supplements")

    for s in payload.get("symptoms") or []:
        conn.execute(
            "INSERT INTO symptoms(user_id, message_id, noted_at, kind, severity, notes,"
            " created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (user_id, message_id, _when(s, default_ts), s.get("kind"), _num(s.get("severity")),
             s.get("notes"), created),
        )
        bump("symptoms")

    for n in payload.get("notes") or []:
        text = n.get("text") if isinstance(n, dict) else str(n)
        if not text:
            continue
        conn.execute(
            "INSERT INTO notes(user_id, message_id, noted_at, text, created_at)"
            " VALUES(?, ?, ?, ?, ?)",
            (user_id, message_id, _when(n if isinstance(n, dict) else {}, default_ts),
             text, created),
        )
        bump("notes")

    conn.commit()
    return counts


# ---------------------------------------------------------------------------
# reading it back
# ---------------------------------------------------------------------------

def daily_totals(conn: sqlite3.Connection, user_id: int, days: int = 7,
                 tz_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """One row per local calendar day, newest last, covering the last `days`
    days including today. Days with nothing logged are included as zeros --
    a gap is the single most informative thing in a food log."""
    tz_name = tz_name or config.timezone_name()
    today = utc_now().astimezone(_zone(tz_name)).date()
    buckets: Dict[str, Dict[str, Any]] = {}
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        buckets[day] = {"day": day, "calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0,
                        "fat_g": 0.0, "meals": 0, "meals_missing_macros": 0,
                        "workouts": 0, "workout_min": 0.0, "water_ml": 0.0,
                        "sleep_h": None, "weight_kg": None}

    since = iso(utc_now() - timedelta(days=days + 2))

    for r in conn.execute(
        "SELECT eaten_at, calories, protein_g, carbs_g, fat_g FROM meals"
        " WHERE user_id = ? AND eaten_at >= ?", (user_id, since)):
        b = buckets.get(local_day(r["eaten_at"], tz_name))
        if b is None:
            continue
        b["meals"] += 1
        # A meal nobody could price is not a zero-calorie meal. Counting it as
        # one would let the day look like a deficit that never happened.
        if r["calories"] is None:
            b["meals_missing_macros"] += 1
        for key in ("calories", "protein_g", "carbs_g", "fat_g"):
            b[key] += r[key] or 0.0

    for r in conn.execute(
        "SELECT started_at, duration_min FROM workouts WHERE user_id = ? AND started_at >= ?",
        (user_id, since)):
        b = buckets.get(local_day(r["started_at"], tz_name))
        if b is None:
            continue
        b["workouts"] += 1
        b["workout_min"] += r["duration_min"] or 0.0

    for r in conn.execute(
        "SELECT drank_at, ml FROM hydration WHERE user_id = ? AND drank_at >= ?",
        (user_id, since)):
        b = buckets.get(local_day(r["drank_at"], tz_name))
        if b is not None:
            b["water_ml"] += r["ml"] or 0.0

    for r in conn.execute(
        "SELECT slept_at, hours FROM sleep_logs WHERE user_id = ? AND slept_at >= ?"
        " ORDER BY id", (user_id, since)):
        b = buckets.get(local_day(r["slept_at"], tz_name))
        if b is not None and r["hours"] is not None:
            b["sleep_h"] = r["hours"]

    for r in conn.execute(
        "SELECT measured_at, weight_kg FROM body_metrics WHERE user_id = ? AND measured_at >= ?"
        " ORDER BY id", (user_id, since)):
        b = buckets.get(local_day(r["measured_at"], tz_name))
        if b is not None and r["weight_kg"] is not None:
            b["weight_kg"] = r["weight_kg"]

    return list(buckets.values())


def recent_workouts(conn: sqlite3.Connection, user_id: int, days: int = 14) -> List[Dict[str, Any]]:
    since = iso(utc_now() - timedelta(days=days))
    return _rows(conn.execute(
        "SELECT * FROM workouts WHERE user_id = ? AND started_at >= ? ORDER BY started_at",
        (user_id, since)))


def last_coach_message(conn: sqlite3.Connection, user_id: int, kind: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM coach_messages WHERE user_id = ? AND kind = ?"
        " ORDER BY sent_at DESC LIMIT 1", (user_id, kind)).fetchone()
    return dict(row) if row else None


def log_coach_message(conn: sqlite3.Connection, user_id: int, kind: str, body: str) -> None:
    conn.execute(
        "INSERT INTO coach_messages(user_id, sent_at, kind, body) VALUES(?, ?, ?, ?)",
        (user_id, now_iso(), kind, body))
    conn.commit()


def counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Row counts per table. Used by `health_bot.py status`."""
    out = {}
    for name in ("users", "messages", "meals", "meal_items", "workouts", "exercise_sets",
                 "body_metrics", "sleep_logs", "hydration", "supplements", "symptoms",
                 "notes", "coach_messages", "extraction_runs"):
        out[name] = conn.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
    return out


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
