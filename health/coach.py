"""
health/coach.py - the part that texts you back.

The whole premise of this project is that the eight-thousand-dollar version of
this is a man sending motivational quotes. So the one thing this coach must not
be is a motivational quote generator. It gets the numbers, only the numbers, and
is told to say when it doesn't have any.

Two kinds of outbound message:

  daily   a digest: what you logged, where it lands against your targets
  nudge   sent only when something is actually true -- e.g. no training logged
          in four days. If nothing is true, nothing is sent. Silence is a
          feature; a bot that texts you every day regardless is noise you will
          mute within a week.

Rate limiting is by (user, kind, local day), enforced against `coach_messages`,
so running this from cron every hour cannot spam you.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from . import config, db, report

SYSTEM = """You are the check-in voice of somebody's own health log. You are
talking to the person who owns the data.

You will be given a block of numbers pulled from their log. That block is
everything you know. Rules:

- Never state a number that is not in the block. Never estimate one.
- If something was not logged, say it was not logged. Do not treat a missing day
  as a zero-calorie day or as a rest day -- you cannot tell those apart.
- No hype, no slogans, no "let's crush it", no emoji walls. Talk like a training
  partner who has seen the numbers: short, specific, a bit dry.
- Say one concrete thing they could do next, drawn from the numbers. If the
  numbers do not support any particular suggestion, say the log is too thin to
  say anything useful and ask for what is missing.
- You are not a doctor. If they logged a symptom that sounds like an injury or
  illness, note it plainly and suggest a professional, without diagnosing.
- Four sentences at most. This is a text message, not an essay.
"""


def _local_today(user: Dict[str, Any]) -> str:
    return db.local_day(db.now_iso(), user.get("tz"))


def already_sent_today(conn: sqlite3.Connection, user: Dict[str, Any], kind: str) -> bool:
    last = db.last_coach_message(conn, user["id"], kind)
    if not last:
        return False
    return db.local_day(last["sent_at"], user.get("tz")) == _local_today(user)


# ---------------------------------------------------------------------------
# is there anything worth saying?
# ---------------------------------------------------------------------------

def nudge_reasons(stat: Dict[str, Any]) -> List[str]:
    """Concrete, checkable facts. Empty list means: send nothing."""
    reasons = []
    daily = stat["daily"]

    trailing = daily[-4:]
    if len(trailing) == 4 and not any(d["workouts"] for d in trailing):
        reasons.append("No training logged in the last 4 days.")

    if stat["days_with_any_log"] <= max(1, stat["days"] // 3):
        reasons.append("Anything logged on only %d of the last %d days."
                       % (stat["days_with_any_log"], stat["days"]))

    # Both macro checks are suppressed when meals went in without a calorie
    # estimate: a "shortfall" that is really a gap in the data is exactly the
    # kind of confident-but-empty number this project exists to avoid.
    unpriced = stat.get("meals_missing_macros") or 0
    protein_target = stat["goals"].get("protein_g")
    avg_protein = stat["avg_protein_logged_days"]
    if protein_target and avg_protein is not None and not unpriced \
            and avg_protein < protein_target * 0.8:
        reasons.append("Protein on logged days averages %.0fg against a %.0fg target."
                       % (avg_protein, protein_target))

    cal_target = stat["goals"].get("calories")
    avg_cal = stat["avg_calories_logged_days"]
    if cal_target and avg_cal is not None and not unpriced \
            and avg_cal > cal_target * 1.15:
        reasons.append("Calories on logged days average %.0f against a %.0f target."
                       % (avg_cal, cal_target))

    if unpriced and stat["days_with_any_log"]:
        reasons.append("No calorie estimate on %s, so nothing can be said about "
                       "intake." % report.plural(unpriced, "logged meal"))

    week_target = stat["goals"].get("workouts_per_week")
    if week_target and stat["sessions"] < week_target:
        reasons.append("%d training sessions in %d days against a target of %g per week."
                       % (stat["sessions"], stat["days"], week_target))

    return reasons


# ---------------------------------------------------------------------------
# wording it
# ---------------------------------------------------------------------------

def _fallback_text(kind: str, lines: List[str], reasons: List[str]) -> str:
    body = "\n".join(lines)
    if kind == "nudge":
        return "Heads up:\n" + "\n".join("- " + r for r in reasons) + "\n\n" + body
    return "Check-in.\n" + body


def compose(kind: str, stat: Dict[str, Any], reasons: List[str],
            model: Optional[str] = None, use_model: bool = True) -> Tuple[str, str]:
    """Return (text, engine). Falls back to the plain numbers if the model call
    is unavailable -- a coach that says nothing beats one that invents."""
    lines = report.summary_lines(stat)
    grounding = lines + ["Flagged: " + r for r in reasons]

    if not use_model:
        return _fallback_text(kind, lines, reasons), "template"

    try:
        import anthropic

        client = anthropic.Anthropic()
        prompt = (
            "Numbers from the log:\n%s\n\n"
            "Write the %s message." % ("\n".join(grounding), kind)
        )
        kwargs: Dict[str, Any] = dict(
            model=model or config.model(),
            max_tokens=1000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low"},
        )
        try:
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        except anthropic.BadRequestError:
            response = client.messages.create(**kwargs)

        if getattr(response, "stop_reason", None) == "refusal":
            return _fallback_text(kind, lines, reasons), "template"
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            return _fallback_text(kind, lines, reasons), "template"
        return text, (model or config.model())
    except ImportError:
        return _fallback_text(kind, lines, reasons), "template"
    except Exception:                                  # noqa: BLE001
        return _fallback_text(kind, lines, reasons), "template"


# ---------------------------------------------------------------------------
# sending
# ---------------------------------------------------------------------------

def run(conn: sqlite3.Connection, kind: str = "daily", days: int = 7,
        send=None, model: Optional[str] = None, use_model: bool = True,
        force: bool = False, dry_run: bool = False) -> List[Dict[str, Any]]:
    """Compose and send one check-in per user. `send(chat_id, text)` does the
    delivery; pass None to compose without sending."""
    out = []
    for user in db.list_users(conn):
        if not force and not dry_run and already_sent_today(conn, user, kind):
            out.append({"user_id": user["id"], "skipped": "already sent today"})
            continue

        stat = report.stats(conn, user, days=days)
        reasons = nudge_reasons(stat)
        if kind == "nudge" and not reasons:
            out.append({"user_id": user["id"], "skipped": "nothing worth saying"})
            continue

        text, engine = compose(kind, stat, reasons, model=model, use_model=use_model)
        result = {"user_id": user["id"], "kind": kind, "engine": engine, "text": text,
                  "reasons": reasons}
        if dry_run:
            result["dry_run"] = True
            out.append(result)
            continue

        if send and user.get("chat_id"):
            send(user["chat_id"], text)
        db.log_coach_message(conn, user["id"], kind, text)
        out.append(result)
    return out
