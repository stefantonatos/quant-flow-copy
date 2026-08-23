"""
health - a Telegram chat that logs what you eat and train into SQLite.

Three moving parts, deliberately kept separate so each can be run, tested and
debugged on its own:

    ingest.py    Telegram in  -> `messages` table (raw, append-only) + a reply
    extract.py   `messages`   -> meals / workouts / sleep / ... (interpreted)
    coach.py     the tables   -> a message back to you

Entry point is `health_bot.py` in the repo root.
"""
