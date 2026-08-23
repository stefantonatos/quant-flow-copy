"""
health/report.py - turning rows back into sentences.

Formatting discipline, borrowed from the trading side of this repo: never show a
flattering aggregate without the thing that qualifies it. A 7-day protein
average means nothing if three of those days have no food logged at all, so days
with no entries are always shown and always counted in the denominator.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from . import db

GOAL_LABELS = {
    "calories": ("kcal", "calories"),
    "protein_g": ("g", "protein"),
    "carbs_g": ("g", "carbs"),
    "fat_g": ("g", "fat"),
    "water_ml": ("ml", "water"),
    "sleep_h": ("h", "sleep"),
    "weight_kg": ("kg", "weight"),
    "workouts_per_week": ("", "workouts/week"),
}


def plural(n: int, word: str) -> str:
    return "%d %s" % (n, word if n == 1 else word + "s")


def _g(value: Optional[float], digits: int = 0) -> str:
    if value is None:
        return "-"
    return ("%%.%df" % digits) % value


def logged_text(result: Dict[str, Any]) -> str:
    """The message sent back once a stored message has been interpreted."""
    counts = result.get("counts") or {}
    if not counts:
        return "Nothing to log from that one -- kept it as a note."
    bits = []
    for table, n in sorted(counts.items()):
        label = table.replace("_", " ")
        bits.append("%d %s" % (n, label if n != 1 else label.rstrip("s")))
    head = "Logged: " + ", ".join(bits)
    summary = (result.get("summary") or "").strip()
    if summary:
        head += "\n" + summary
    if result.get("engine") == "heuristic":
        head += "\n(keyword matching only -- no macros estimated)"
    return head


def day_report(conn: sqlite3.Connection, user: Dict[str, Any]) -> str:
    rows = db.daily_totals(conn, user["id"], days=1, tz_name=user.get("tz"))
    today = rows[-1]
    targets = db.goals(conn, user["id"])
    lines = ["Today (%s)" % today["day"]]
    if today["meals"] == 0 and today["workouts"] == 0:
        lines.append("  nothing logged yet")
    if today["meals"]:
        lines.append("  food: %s, %s kcal, %sg protein, %sg carbs, %sg fat" % (
            plural(today["meals"], "meal"), _g(today["calories"]),
            _g(today["protein_g"]), _g(today["carbs_g"]), _g(today["fat_g"])))
        if today["meals_missing_macros"]:
            lines.append("  (%s logged with no calorie estimate, so those totals "
                         "are floors, not sums)"
                         % plural(today["meals_missing_macros"], "meal"))
    if today["workouts"]:
        lines.append("  training: %s, %s min" % (
            plural(today["workouts"], "session"), _g(today["workout_min"])))
    if today["water_ml"]:
        lines.append("  water: %s ml" % _g(today["water_ml"]))
    if today["sleep_h"] is not None:
        lines.append("  sleep: %sh" % _g(today["sleep_h"], 1))
    if today["weight_kg"] is not None:
        lines.append("  weight: %s kg" % _g(today["weight_kg"], 1))

    for kind, target in sorted(targets.items()):
        unit, label = GOAL_LABELS.get(kind, ("", kind))
        actual = {"calories": today["calories"], "protein_g": today["protein_g"],
                  "carbs_g": today["carbs_g"], "fat_g": today["fat_g"],
                  "water_ml": today["water_ml"], "sleep_h": today["sleep_h"],
                  "weight_kg": today["weight_kg"]}.get(kind)
        if actual is None:
            continue
        line = "  %s: %s / %s %s (%+d)" % (
            label, _g(actual), _g(target), unit, round(actual - target))
        if kind in ("calories", "protein_g", "carbs_g", "fat_g") \
                and today["meals_missing_macros"]:
            line += " -- incomplete"
        lines.append(line)
    return "\n".join(lines)


def week_report(conn: sqlite3.Connection, user: Dict[str, Any], days: int = 7) -> str:
    rows = db.daily_totals(conn, user["id"], days=days, tz_name=user.get("tz"))
    lines = ["Last %d days" % days,
             "  day         kcal  prot  train  logged"]
    logged_days = 0
    for r in rows:
        logged = r["meals"] > 0 or r["workouts"] > 0
        logged_days += 1 if logged else 0
        lines.append("  %s  %5s  %4s  %5s  %s" % (
            r["day"], _g(r["calories"]), _g(r["protein_g"]),
            _g(r["workout_min"]), "yes" if logged else "NO"))

    lines.append("")
    lines.append("  Anything logged on %d of %d days." % (logged_days, days))
    if logged_days:
        avg_cal = sum(r["calories"] for r in rows) / logged_days
        avg_pro = sum(r["protein_g"] for r in rows) / logged_days
        lines.append("  Averages over LOGGED days only: %s kcal, %sg protein."
                     % (_g(avg_cal), _g(avg_pro)))
        if logged_days < days:
            lines.append("  Unlogged days are excluded, so these averages describe "
                         "your logging, not your eating.")
    unpriced = sum(r["meals_missing_macros"] for r in rows)
    if unpriced:
        lines.append("  No calorie estimate on %s in the window; every kcal and "
                     "protein figure above is a floor." % plural(unpriced, "meal"))
    sessions = sum(r["workouts"] for r in rows)
    lines.append("  %s in the window." % plural(sessions, "training session"))
    return "\n".join(lines)


def stats(conn: sqlite3.Connection, user: Dict[str, Any], days: int = 7) -> Dict[str, Any]:
    """The numbers the coach is allowed to talk about. Anything not in here, it
    does not know, and the prompt tells it to say so rather than guess."""
    rows = db.daily_totals(conn, user["id"], days=days, tz_name=user.get("tz"))
    logged = [r for r in rows if r["meals"] or r["workouts"]]
    workouts = db.recent_workouts(conn, user["id"], days=days)
    weights = [r["weight_kg"] for r in rows if r["weight_kg"] is not None]
    return {
        "days": days,
        "days_with_any_log": len(logged),
        "today": rows[-1],
        "daily": rows,
        "sessions": len(workouts),
        "session_focus": [w.get("focus") or w.get("kind") or "unspecified" for w in workouts],
        "avg_calories_logged_days": (sum(r["calories"] for r in logged) / len(logged)
                                     if logged else None),
        "avg_protein_logged_days": (sum(r["protein_g"] for r in logged) / len(logged)
                                    if logged else None),
        "meals_missing_macros": sum(r["meals_missing_macros"] for r in rows),
        "weight_first": weights[0] if weights else None,
        "weight_last": weights[-1] if weights else None,
        "goals": db.goals(conn, user["id"]),
    }


def summary_lines(stat: Dict[str, Any]) -> List[str]:
    """Deterministic prose for the same numbers -- used as the coach's fallback
    and as the grounding block in its prompt."""
    lines = []
    lines.append("Window: last %d days. Days with anything logged: %d of %d."
                 % (stat["days"], stat["days_with_any_log"], stat["days"]))
    if stat["avg_calories_logged_days"] is not None:
        lines.append("Average on logged days: %s kcal, %sg protein."
                     % (_g(stat["avg_calories_logged_days"]),
                        _g(stat["avg_protein_logged_days"])))
    if stat.get("meals_missing_macros"):
        lines.append("No calorie estimate on %s in the window, so the kcal and "
                     "protein figures are floors, not totals."
                     % plural(stat["meals_missing_macros"], "meal"))
    lines.append("Training sessions: %d (%s)." % (
        stat["sessions"], ", ".join(stat["session_focus"]) or "none"))
    if stat["weight_first"] is not None and stat["weight_last"] is not None:
        lines.append("Weight: %s -> %s kg." % (_g(stat["weight_first"], 1),
                                               _g(stat["weight_last"], 1)))
    for kind, target in sorted(stat["goals"].items()):
        unit, label = GOAL_LABELS.get(kind, ("", kind))
        lines.append("Target %s: %g %s." % (label, target, unit))
    return lines
