"""
health/ingest.py - Telegram in, `messages` table out.

This half of the pipeline is deliberately dumb. It does not try to understand
anything you send; it stores the message verbatim, confirms receipt, and stops.
Interpretation is extract.py's job, and keeping the two apart means a bad model
response can never cost you the original sentence.

Order of operations matters here:

    1. store the message      (durable)
    2. advance the offset     (durable)
    3. send the confirmation  (best effort)

If step 3 fails, you lose a reply. If steps 1 and 2 were swapped, you would lose
the message itself. Telegram may redeliver an update whose offset never landed,
which is why `db.add_message` de-duplicates on (chat_id, telegram_message_id).
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional

from . import config, db, transcribe as stt
from .telegram import TelegramClient, TelegramError, parse_message

OFFSET_KEY = "telegram_offset"

HELP = (
    "Health log bot.\n\n"
    "Just talk to me. Text or voice:\n"
    "  \"ate tandoori chicken and rice for lunch\"\n"
    "  \"short legs workout, 25 min, squats 3x8 at 100kg\"\n"
    "  \"weighed 82.4 this morning\"\n"
    "  \"slept 6 hours, rough night\"\n\n"
    "Commands:\n"
    "  /today   what you have logged today\n"
    "  /week    the last 7 days\n"
    "  /goals   your current targets\n"
    "  /goal calories 2600   set a target\n"
    "           (calories, protein_g, water_ml, sleep_h, workouts_per_week, weight_kg)\n"
    "  /help    this message"
)


def _confirmation(kind: str, status: str) -> str:
    if status == "needs_transcription":
        return ("Got your voice note and saved it, but no transcriber is configured "
                "so I can't read it yet. Send it as text if you want it counted today.")
    if kind == "voice":
        return "Got the voice note, transcribed it. Sorting it into your log now."
    if kind == "photo":
        return "Saved the photo. I can't read food photos yet -- add a caption and I'll log that."
    return "Got it, saved. Sorting it into your log now."


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

GOAL_KINDS = ("calories", "protein_g", "carbs_g", "fat_g", "water_ml", "sleep_h",
              "workouts_per_week", "weight_kg")


def handle_command(conn: sqlite3.Connection, user: Dict[str, Any], text: str) -> str:
    """Slash commands are answered here and never reach the extractor."""
    from . import report  # local import: report imports db, not ingest

    parts = text.split()
    cmd = parts[0].lower().split("@")[0]

    if cmd in ("/start", "/help"):
        return HELP
    if cmd == "/today":
        return report.day_report(conn, user)
    if cmd == "/week":
        return report.week_report(conn, user)
    if cmd == "/goals":
        current = db.goals(conn, user["id"])
        if not current:
            return "No targets set. Try: /goal calories 2600"
        return "Targets:\n" + "\n".join(
            "  %s: %g" % (k, v) for k, v in sorted(current.items()))
    if cmd == "/goal":
        if len(parts) < 3:
            return "Usage: /goal calories 2600\nKinds: " + ", ".join(GOAL_KINDS)
        kind = parts[1].lower()
        if kind not in GOAL_KINDS:
            return "I don't track '%s'. Kinds: %s" % (kind, ", ".join(GOAL_KINDS))
        try:
            target = float(parts[2].replace(",", "."))
        except ValueError:
            return "'%s' is not a number." % parts[2]
        db.set_goal(conn, user["id"], kind, target)
        return "Target set: %s = %g" % (kind, target)
    return "Unknown command. /help lists what I understand."


# ---------------------------------------------------------------------------
# one update
# ---------------------------------------------------------------------------

def handle_update(conn: sqlite3.Connection, tg: TelegramClient, update: Dict[str, Any],
                  allowed: Optional[set] = None,
                  media_dir: Optional[str] = None) -> Dict[str, Any]:
    """Store one Telegram update and reply. Returns a small dict describing what
    happened, which is what the tests and the CLI report on."""
    parsed = parse_message(update)
    if parsed is None:
        return {"skipped": "unsupported update"}

    chat_id = parsed["chat_id"]
    if allowed and chat_id not in allowed and parsed["telegram_user_id"] not in allowed:
        # Silent drop, not a reply: answering tells a stranger the bot is live.
        return {"skipped": "chat %s not allowed" % chat_id}

    user = db.upsert_user(conn, parsed["telegram_user_id"], chat_id, parsed["name"])

    text = (parsed.get("text") or "").strip()
    if text.startswith("/"):
        db.add_message(conn, chat_id=chat_id, user_id=user["id"],
                       telegram_message_id=parsed["telegram_message_id"],
                       kind="text", text=text, status="ignored")
        reply = handle_command(conn, user, text)
        _try_send(tg, chat_id, reply, parsed["telegram_message_id"])
        return {"command": text.split()[0], "user_id": user["id"]}

    status = "pending"
    file_path = None
    transcript = None
    error = None

    if parsed["kind"] == "voice" and parsed["file_id"]:
        if stt.available():
            try:
                file_path = tg.download(parsed["file_id"], media_dir or config.media_dir())
                transcript = stt.transcribe(file_path)
            except (TelegramError, RuntimeError) as exc:
                status, error = "needs_transcription", str(exc)
        else:
            status = "needs_transcription"
    elif parsed["kind"] == "photo":
        # Stored for the record, but there is nothing to interpret without a
        # caption -- claiming otherwise would put invented calories in the db.
        status = "pending" if text else "needs_transcription"

    message_id = db.add_message(
        conn, chat_id=chat_id, user_id=user["id"],
        telegram_message_id=parsed["telegram_message_id"],
        kind=parsed["kind"], text=text or None, transcript=transcript,
        file_id=parsed["file_id"], file_path=file_path,
        duration_s=parsed["duration_s"], status=status,
    )
    if message_id is None:
        return {"duplicate": parsed["telegram_message_id"]}
    if error:
        conn.execute("UPDATE messages SET error = ? WHERE id = ?", (error, message_id))
        conn.commit()

    _try_send(tg, chat_id, _confirmation(parsed["kind"], status), parsed["telegram_message_id"])
    return {"message_id": message_id, "user_id": user["id"], "status": status,
            "kind": parsed["kind"]}


def _try_send(tg: TelegramClient, chat_id: int, text: str, reply_to: Optional[int]) -> None:
    """A failed reply must not roll back a stored message."""
    try:
        tg.send_message(chat_id, text, reply_to=reply_to)
    except TelegramError:
        pass


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------

def poll_once(conn: sqlite3.Connection, tg: TelegramClient, timeout: int = 30,
              allowed: Optional[set] = None) -> List[Dict[str, Any]]:
    """One getUpdates round trip. Returns the per-update results."""
    offset = db.get_state(conn, OFFSET_KEY)
    updates = tg.get_updates(offset=int(offset) if offset else None, timeout=timeout)
    results = []
    for update in updates:
        try:
            results.append(handle_update(conn, tg, update, allowed=allowed))
        except Exception as exc:                       # noqa: BLE001 - see below
            # One malformed update must not stall the queue forever: record it,
            # advance past it, keep going.
            results.append({"error": "%s: %s" % (type(exc).__name__, exc)})
        if update.get("update_id") is not None:
            db.set_state(conn, OFFSET_KEY, int(update["update_id"]) + 1)
    return results


def run(conn: sqlite3.Connection, tg: TelegramClient, timeout: int = 30,
        allowed: Optional[set] = None, once: bool = False,
        on_batch: Optional[Callable[[List[Dict[str, Any]]], None]] = None) -> None:
    """Long-poll until interrupted."""
    backoff = 1.0
    while True:
        try:
            results = poll_once(conn, tg, timeout=timeout, allowed=allowed)
            backoff = 1.0
            if results and on_batch:
                on_batch(results)
        except TelegramError as exc:
            print("telegram: %s -- retrying in %.0fs" % (exc, backoff))
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        if once:
            return
