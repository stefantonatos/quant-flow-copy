-- health/schema.sql - the store behind the Telegram health chat.
--
-- Design rules, so future edits don't drift:
--   1. `messages` is the append-only inbox. Nothing deletes from it. Every
--      structured row points back at the message it came from, so any number
--      in a report can be traced to the sentence you actually said.
--   2. Extraction is allowed to be wrong. That's why `confidence` exists on the
--      interpreted tables and why `extraction_runs` keeps the raw model output.
--   3. Times are stored as ISO-8601 UTC strings ('YYYY-MM-DDTHH:MM:SSZ').
--      Local-time display is a presentation concern, handled in Python.
--   4. Everything is CREATE ... IF NOT EXISTS so this file doubles as the
--      migration for an existing database.

PRAGMA foreign_keys = ON;

-- Who is allowed to talk to the bot. One row per Telegram account.
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY,
    telegram_id  INTEGER NOT NULL UNIQUE,
    chat_id      INTEGER,
    name         TEXT,
    tz           TEXT NOT NULL DEFAULT 'UTC',
    created_at   TEXT NOT NULL
);

-- Targets the coach measures you against. kind is free text but the coach
-- understands: calories, protein_g, carbs_g, fat_g, water_ml, sleep_h,
-- workouts_per_week, weight_kg.
CREATE TABLE IF NOT EXISTS goals (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    kind       TEXT    NOT NULL,
    target     REAL    NOT NULL,
    unit       TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL,
    UNIQUE(user_id, kind)
);

-- The inbox. Raw inbound Telegram messages, exactly as received.
-- status: pending | processed | needs_transcription | failed | ignored
CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY,
    user_id             INTEGER REFERENCES users(id),
    chat_id             INTEGER NOT NULL,
    telegram_message_id INTEGER,
    kind                TEXT    NOT NULL DEFAULT 'text',   -- text | voice | photo | other
    text                TEXT,                              -- what was typed
    transcript          TEXT,                              -- what was said, if transcribed
    file_id             TEXT,                              -- Telegram file id for voice/photo
    file_path           TEXT,                              -- local path once downloaded
    duration_s          INTEGER,
    status              TEXT    NOT NULL DEFAULT 'pending',
    error               TEXT,
    received_at         TEXT    NOT NULL,
    processed_at        TEXT,
    UNIQUE(chat_id, telegram_message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status, id);

-- What you ate.
CREATE TABLE IF NOT EXISTS meals (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    message_id  INTEGER REFERENCES messages(id),
    eaten_at    TEXT    NOT NULL,
    meal_type   TEXT,                                      -- breakfast | lunch | dinner | snack
    description TEXT,
    calories    REAL,
    protein_g   REAL,
    carbs_g     REAL,
    fat_g       REAL,
    confidence  REAL,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meals_user_time ON meals(user_id, eaten_at);

-- Per-food breakdown of a meal. Optional: a meal can carry totals only.
CREATE TABLE IF NOT EXISTS meal_items (
    id        INTEGER PRIMARY KEY,
    meal_id   INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    quantity  REAL,
    unit      TEXT,
    calories  REAL,
    protein_g REAL,
    carbs_g   REAL,
    fat_g     REAL
);

-- What you trained.
CREATE TABLE IF NOT EXISTS workouts (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    message_id       INTEGER REFERENCES messages(id),
    started_at       TEXT    NOT NULL,
    kind             TEXT,                                 -- strength | cardio | mobility | sport
    focus            TEXT,                                 -- legs | push | pull | full body | 5k ...
    duration_min     REAL,
    intensity        TEXT,                                 -- light | moderate | hard
    perceived_effort REAL,                                 -- RPE 1-10
    calories_est     REAL,
    notes            TEXT,
    confidence       REAL,
    created_at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workouts_user_time ON workouts(user_id, started_at);

-- Set-by-set detail, when the message was specific enough to have it.
CREATE TABLE IF NOT EXISTS exercise_sets (
    id          INTEGER PRIMARY KEY,
    workout_id  INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    exercise    TEXT NOT NULL,
    set_no      INTEGER,
    reps        REAL,
    weight_kg   REAL,
    rpe         REAL,
    distance_km REAL,
    duration_s  REAL
);

-- Scale and tape-measure readings.
CREATE TABLE IF NOT EXISTS body_metrics (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    message_id   INTEGER REFERENCES messages(id),
    measured_at  TEXT    NOT NULL,
    weight_kg    REAL,
    body_fat_pct REAL,
    waist_cm     REAL,
    resting_hr   REAL,
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_body_user_time ON body_metrics(user_id, measured_at);

CREATE TABLE IF NOT EXISTS sleep_logs (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    message_id INTEGER REFERENCES messages(id),
    slept_at   TEXT    NOT NULL,                           -- the night it covers
    hours      REAL,
    quality    TEXT,                                       -- poor | ok | good
    notes      TEXT,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS hydration (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    message_id INTEGER REFERENCES messages(id),
    drank_at   TEXT    NOT NULL,
    ml         REAL,
    drink      TEXT,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS supplements (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    message_id INTEGER REFERENCES messages(id),
    taken_at   TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    dose       REAL,
    unit       TEXT,
    created_at TEXT    NOT NULL
);

-- Aches, injuries, illness, "knee felt weird on squats".
CREATE TABLE IF NOT EXISTS symptoms (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    message_id INTEGER REFERENCES messages(id),
    noted_at   TEXT    NOT NULL,
    kind       TEXT,
    severity   REAL,                                       -- 1-10
    notes      TEXT,
    created_at TEXT    NOT NULL
);

-- Catch-all so nothing you said is silently dropped.
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    message_id INTEGER REFERENCES messages(id),
    noted_at   TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

-- Everything the coach has sent, so it can avoid repeating itself and you can
-- audit what it claimed.
CREATE TABLE IF NOT EXISTS coach_messages (
    id      INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    sent_at TEXT    NOT NULL,
    kind    TEXT    NOT NULL,                              -- daily | nudge | reply
    body    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coach_user_time ON coach_messages(user_id, sent_at);

-- Audit trail for the interpretation step. Keeps the raw model output so a
-- wrong row can be explained rather than guessed at.
CREATE TABLE IF NOT EXISTS extraction_runs (
    id         INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    engine     TEXT    NOT NULL,                           -- model id, or 'heuristic'
    ok         INTEGER NOT NULL,
    raw_json   TEXT,
    error      TEXT,
    created_at TEXT    NOT NULL
);

-- Key/value scratch space. Holds the Telegram getUpdates offset so restarts
-- don't replay or skip messages.
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Daily nutrition rollup. Date is UTC; good enough for trend, and the report
-- layer re-buckets into local time when a timezone is set.
CREATE VIEW IF NOT EXISTS v_daily_nutrition AS
SELECT user_id,
       substr(eaten_at, 1, 10) AS day,
       COUNT(*)                AS meals,
       SUM(calories)           AS calories,
       SUM(protein_g)          AS protein_g,
       SUM(carbs_g)            AS carbs_g,
       SUM(fat_g)              AS fat_g
FROM meals
GROUP BY user_id, day;

CREATE VIEW IF NOT EXISTS v_daily_training AS
SELECT user_id,
       substr(started_at, 1, 10) AS day,
       COUNT(*)                  AS sessions,
       SUM(duration_min)         AS duration_min,
       SUM(calories_est)         AS calories_est
FROM workouts
GROUP BY user_id, day;
