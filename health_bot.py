"""
health_bot.py - CLI for the Telegram health chat.

Setup, once:
  1. Talk to @BotFather in Telegram, /newbot, copy the token.
  2. export TELEGRAM_BOT_TOKEN=...
  3. Message your new bot once, then:  python health_bot.py whoami
     It prints the chat id of whoever wrote. Then:
     export TELEGRAM_ALLOWED_CHATS=<that id>
  4. export ANTHROPIC_API_KEY=...        (without it, keyword matching only)
  5. python health_bot.py init

Day to day, two processes:
  python health_bot.py ingest --process    listen, store, confirm, interpret
  python health_bot.py coach --kind nudge  from cron, e.g. hourly

Everything else:
  python health_bot.py status                    row counts and queue depth
  python health_bot.py log "ate chicken and rice, 30 min legs"
                                                 inject a message without Telegram
  python health_bot.py process --dry-run         show what the model would write
  python health_bot.py report --days 7
  python health_bot.py goal calories 2600
  python health_bot.py users
  python health_bot.py whoami                    print chat ids currently writing

Flags common to all commands:
  --db PATH        sqlite file (default: $HEALTH_DB, else ./health.db)
  --no-model       never call Claude; use the keyword fallback
  --model ID       override $HEALTH_MODEL (default: claude-opus-5)
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from health import coach as coach_mod
from health import config, db, extract, ingest, report
from health.telegram import TelegramClient, TelegramError


def _client(args) -> TelegramClient:
    return TelegramClient(config.bot_token())


def _safe_send(tg: TelegramClient):
    """A send that cannot take the listener down with it. Losing one reply is
    survivable; losing the poll loop means messages pile up unseen."""
    def send(chat_id, text):
        try:
            tg.send_message(chat_id, text)
        except TelegramError as exc:
            print("  send failed: %s" % exc)
    return send


def _pick_user(conn, args):
    users = db.list_users(conn)
    if not users:
        return None
    if getattr(args, "user", None):
        for u in users:
            if u["id"] == args.user or u["telegram_id"] == args.user:
                return u
        return None
    return users[0]


# ---------------------------------------------------------------------------

def cmd_init(conn, args) -> int:
    print("database ready: %s" % (args.db or config.db_path()))
    for name, n in sorted(db.counts(conn).items()):
        print("  %-16s %d" % (name, n))
    return 0


def cmd_status(conn, args) -> int:
    counts = db.counts(conn)
    pending = len(db.pending_messages(conn, limit=10000))
    print("db: %s" % (args.db or config.db_path()))
    print("pending messages: %d" % pending)
    for name, n in sorted(counts.items()):
        print("  %-16s %d" % (name, n))
    offset = db.get_state(conn, ingest.OFFSET_KEY)
    print("telegram offset: %s" % (offset or "(none yet)"))
    print("model: %s" % config.model())
    print("transcriber: %s" % (config.transcribe_cmd() or "(not configured)"))
    return 0


def cmd_whoami(conn, args) -> int:
    """Print who is messaging the bot, so you can fill TELEGRAM_ALLOWED_CHATS."""
    tg = _client(args)
    me = tg.get_me()
    print("bot: @%s (%s)" % (me.get("username"), me.get("id")))
    offset = db.get_state(conn, ingest.OFFSET_KEY)
    updates = tg.get_updates(offset=int(offset) if offset else None, timeout=args.timeout)
    if not updates:
        print("no pending updates -- send your bot a message, then run this again.")
        return 0
    seen = {}
    for u in updates:
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        if chat.get("id"):
            seen[chat["id"]] = sender.get("username") or sender.get("first_name") or "?"
    for chat_id, who in seen.items():
        print("chat_id %s  (%s)" % (chat_id, who))
    print("\nexport TELEGRAM_ALLOWED_CHATS=%s" % ",".join(str(k) for k in seen))
    print("(these updates were NOT consumed; the offset is unchanged)")
    return 0


def cmd_ingest(conn, args) -> int:
    tg = _client(args)
    allowed = config.allowed_chats()
    if not allowed:
        print("WARNING: TELEGRAM_ALLOWED_CHATS is empty -- anyone who finds this "
              "bot can write to your health log.")
    print("listening (ctrl-c to stop)...")

    def on_batch(results):
        for r in results:
            print("  %s" % r)
        if args.process:
            done = extract.process_pending(
                conn, limit=args.limit, model=args.model,
                use_model=not args.no_model, notify=_safe_send(tg))
            for d in done:
                print("  processed: %s" % d)

    try:
        ingest.run(conn, tg, timeout=args.timeout, allowed=allowed, once=args.once,
                   on_batch=on_batch)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


def cmd_process(conn, args) -> int:
    notify = None
    if args.notify and not args.dry_run:
        notify = _safe_send(_client(args))
    results = extract.process_pending(conn, limit=args.limit, model=args.model,
                                      use_model=not args.no_model,
                                      dry_run=args.dry_run, notify=notify)
    if not results:
        print("nothing pending.")
    for r in results:
        if r.get("dry_run"):
            print("message %s via %s:" % (r["message_id"], r["engine"]))
            print(db.dumps(r["payload"]))
        else:
            print(r)
    return 0


def cmd_log(conn, args) -> int:
    """Inject a message as if it had arrived from Telegram. The point of entry
    for testing the whole pipeline with no bot token at all."""
    telegram_id = args.user or 0
    user = db.upsert_user(conn, telegram_id, chat_id=telegram_id, name="local")
    message_id = db.add_message(conn, chat_id=user["chat_id"] or 0, user_id=user["id"],
                                telegram_message_id=None, text=args.text)
    print("stored message %s" % message_id)
    if args.process:
        msg = dict(conn.execute("SELECT * FROM messages WHERE id = ?",
                                (message_id,)).fetchone())
        result = extract.process_message(conn, msg, model=args.model,
                                         use_model=not args.no_model,
                                         dry_run=args.dry_run)
        if args.dry_run:
            print(db.dumps(result["payload"]))
        else:
            print(report.logged_text(result))
    return 0


def cmd_report(conn, args) -> int:
    user = _pick_user(conn, args)
    if not user:
        print("no users yet -- send the bot a message, or use `log`.")
        return 1
    print(report.day_report(conn, user))
    print()
    print(report.week_report(conn, user, days=args.days))
    return 0


def cmd_coach(conn, args) -> int:
    send = None
    if not args.dry_run:
        try:
            send = _safe_send(_client(args))
        except RuntimeError as exc:
            print("not sending (%s); showing instead." % exc)
    results = coach_mod.run(conn, kind=args.kind, days=args.days, send=send,
                            model=args.model, use_model=not args.no_model,
                            force=args.force, dry_run=args.dry_run)
    for r in results:
        if r.get("skipped"):
            print("user %s: skipped (%s)" % (r["user_id"], r["skipped"]))
        else:
            print("--- user %s [%s via %s] ---\n%s" % (r["user_id"], r["kind"],
                                                       r["engine"], r["text"]))
    return 0


def cmd_goal(conn, args) -> int:
    user = _pick_user(conn, args)
    if not user:
        print("no users yet -- send the bot a message, or use `log`.")
        return 1
    db.set_goal(conn, user["id"], args.kind, args.value)
    print("%s: %s = %g" % (user.get("name") or user["telegram_id"], args.kind, args.value))
    return 0


def cmd_users(conn, args) -> int:
    users = db.list_users(conn)
    if not users:
        print("(none)")
    for u in users:
        print("id=%s telegram=%s chat=%s name=%s tz=%s" % (
            u["id"], u["telegram_id"], u["chat_id"], u["name"], u["tz"]))
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="health_bot", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None, help="sqlite path")
    p.add_argument("--model", default=None, help="Claude model id")
    p.add_argument("--no-model", action="store_true", help="keyword fallback only")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create or migrate the database")
    sub.add_parser("status", help="row counts and queue depth")
    sub.add_parser("users", help="list known users")

    who = sub.add_parser("whoami", help="show chat ids currently messaging the bot")
    who.add_argument("--timeout", type=int, default=2)

    ing = sub.add_parser("ingest", help="long-poll Telegram and store messages")
    ing.add_argument("--once", action="store_true", help="one poll, then exit")
    ing.add_argument("--timeout", type=int, default=30, help="long-poll seconds")
    ing.add_argument("--process", action="store_true",
                     help="interpret each batch immediately after storing it")
    ing.add_argument("--limit", type=int, default=25)

    proc = sub.add_parser("process", help="interpret pending messages")
    proc.add_argument("--limit", type=int, default=25)
    proc.add_argument("--dry-run", action="store_true", help="show rows, write nothing")
    proc.add_argument("--notify", action="store_true", help="send the breakdown to Telegram")

    lg = sub.add_parser("log", help="inject a message without Telegram")
    lg.add_argument("text")
    lg.add_argument("--user", type=int, default=None, help="telegram id (default 0)")
    lg.add_argument("--process", action="store_true", default=True)
    lg.add_argument("--store-only", dest="process", action="store_false")
    lg.add_argument("--dry-run", action="store_true")

    rep = sub.add_parser("report", help="print today and the last N days")
    rep.add_argument("--days", type=int, default=7)
    rep.add_argument("--user", type=int, default=None)

    co = sub.add_parser("coach", help="compose and send a check-in")
    co.add_argument("--kind", choices=("daily", "nudge"), default="daily")
    co.add_argument("--days", type=int, default=7)
    co.add_argument("--dry-run", action="store_true")
    co.add_argument("--force", action="store_true", help="ignore the once-a-day limit")

    goal = sub.add_parser("goal", help="set a target")
    goal.add_argument("kind", choices=ingest.GOAL_KINDS)
    goal.add_argument("value", type=float)
    goal.add_argument("--user", type=int, default=None)

    return p


HANDLERS = {
    "init": cmd_init, "status": cmd_status, "users": cmd_users, "whoami": cmd_whoami,
    "ingest": cmd_ingest, "process": cmd_process, "log": cmd_log, "report": cmd_report,
    "coach": cmd_coach, "goal": cmd_goal,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    conn = db.connect(args.db)
    try:
        return HANDLERS[args.command](conn, args)
    except RuntimeError as exc:
        print("error: %s" % exc)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
