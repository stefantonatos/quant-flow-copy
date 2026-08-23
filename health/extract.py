"""
health/extract.py - pending messages in, typed rows out.

This is the step that reads "oh I ate some tandoori chicken, dude, and had a
very short legs workout" and decides that it is one meal row plus one workout
row, not one note row.

Two engines, same output shape:

  * Claude (default). One request per message, with a JSON schema attached via
    `output_config.format`, so the response is guaranteed-parseable JSON rather
    than prose we have to regex.
  * A keyword heuristic (fallback). Used when the `anthropic` package or an API
    key is missing. It is genuinely crude -- it catches "ate/had/lunch" and
    "workout/gym/ran/squat" and gives up on macros entirely. It exists so the
    pipeline runs and can be tested offline, not because it is good.

Whichever ran is recorded in `extraction_runs.engine`, together with the raw
response, so a wrong row can always be traced back to what produced it.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from . import config, db

# --- the contract with the model -------------------------------------------
# One key per destination table. Every key is required (an empty list is a
# perfectly good answer), which removes the "did it forget or did it mean none?"
# ambiguity from every downstream check.

_NUM = {"type": ["number", "null"]}
_STR = {"type": ["string", "null"]}

EXTRACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["meals", "workouts", "body_metrics", "sleep", "hydration",
                 "supplements", "symptoms", "notes", "summary"],
    "properties": {
        "meals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["when", "meal_type", "description", "items", "calories",
                             "protein_g", "carbs_g", "fat_g", "confidence"],
                "properties": {
                    "when": _STR,
                    "meal_type": _STR,
                    "description": _STR,
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "quantity", "unit", "calories",
                                         "protein_g", "carbs_g", "fat_g"],
                            "properties": {
                                "name": {"type": "string"},
                                "quantity": _NUM, "unit": _STR, "calories": _NUM,
                                "protein_g": _NUM, "carbs_g": _NUM, "fat_g": _NUM,
                            },
                        },
                    },
                    "calories": _NUM, "protein_g": _NUM, "carbs_g": _NUM, "fat_g": _NUM,
                    "confidence": _NUM,
                },
            },
        },
        "workouts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["when", "kind", "focus", "duration_min", "intensity",
                             "perceived_effort", "calories_est", "notes", "sets",
                             "confidence"],
                "properties": {
                    "when": _STR, "kind": _STR, "focus": _STR, "duration_min": _NUM,
                    "intensity": _STR, "perceived_effort": _NUM, "calories_est": _NUM,
                    "notes": _STR,
                    "sets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["exercise", "set_no", "reps", "weight_kg",
                                         "rpe", "distance_km", "duration_s"],
                            "properties": {
                                "exercise": {"type": "string"},
                                "set_no": _NUM, "reps": _NUM, "weight_kg": _NUM,
                                "rpe": _NUM, "distance_km": _NUM, "duration_s": _NUM,
                            },
                        },
                    },
                    "confidence": _NUM,
                },
            },
        },
        "body_metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["when", "weight_kg", "body_fat_pct", "waist_cm", "resting_hr"],
                "properties": {"when": _STR, "weight_kg": _NUM, "body_fat_pct": _NUM,
                               "waist_cm": _NUM, "resting_hr": _NUM},
            },
        },
        "sleep": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["when", "hours", "quality", "notes"],
                "properties": {"when": _STR, "hours": _NUM, "quality": _STR, "notes": _STR},
            },
        },
        "hydration": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["when", "ml", "drink"],
                "properties": {"when": _STR, "ml": _NUM, "drink": _STR},
            },
        },
        "supplements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["when", "name", "dose", "unit"],
                "properties": {"when": _STR, "name": {"type": "string"}, "dose": _NUM,
                               "unit": _STR},
            },
        },
        "symptoms": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["when", "kind", "severity", "notes"],
                "properties": {"when": _STR, "kind": _STR, "severity": _NUM, "notes": _STR},
            },
        },
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["when", "text"],
                "properties": {"when": _STR, "text": {"type": "string"}},
            },
        },
        "summary": {"type": "string"},
    },
}

SYSTEM = """You turn short, casual health messages into structured log rows.

The person is texting a bot about their day. Messages are sloppy, often voice
transcripts, sometimes several things at once: "oh I ate some tandoori chicken
dude, and had a very short legs workout".

Return one entry per real-world event, in the matching array:
  meals         anything eaten or drunk with calories
  workouts      any training session; put set-by-set detail in `sets`
  body_metrics  scale weight, body fat, waist, resting heart rate
  sleep         hours slept and how it went
  hydration     water, tea, anything non-caloric they drank, in ml
  supplements   creatine, vitamins, protein powder taken as a supplement
  symptoms      pain, injury, illness, "knee felt off"
  notes         anything health-related that fits nowhere else

Rules that matter:
- Estimate calories and macros when the food is identifiable. A rough number is
  more useful than a null, but say how sure you are: `confidence` is 0.0-1.0 and
  should be low (0.2-0.4) for a vague description like "some chicken", higher
  (0.7-0.9) when quantities are given.
- Never invent detail that was not implied. If they did not say how long the
  workout was, `duration_min` is null. "Very short" is a real signal: roughly
  15-20 minutes, low confidence -- that is an inference, not a fabrication.
- `when` is an ISO-8601 UTC timestamp, or null to mean "when the message was
  sent". Resolve relative phrases ("this morning", "yesterday", "after work")
  against the message time given below, in the person's local timezone.
- Weights default to kg and volumes to ml unless stated otherwise.
- Do not log intentions. "Going to the gym later" is a note, not a workout.
  Log a workout only for training that has happened.
- If a message is not about health at all, return empty arrays everywhere.
- `summary`: one short line, plain and factual, saying what you logged. No
  praise, no motivation. Example: "Logged lunch (~620 kcal, 55g protein) and a
  20 min leg session."
"""


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _client():
    import anthropic  # imported lazily: the rest of the package is stdlib-only

    return anthropic.Anthropic()


def claude_payload(text: str, message_time: str, tz_name: str,
                   model: Optional[str] = None) -> Tuple[Dict[str, Any], str, str]:
    """Ask Claude to structure one message. Returns (payload, engine, raw_json)."""
    import anthropic

    model = model or config.model()
    client = _client()
    prompt = (
        "Message sent at %s (UTC). The person's timezone is %s.\n\n"
        "Message:\n%s" % (message_time, tz_name, text)
    )
    kwargs: Dict[str, Any] = dict(
        model=model,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={
            "effort": "low",          # short classification, not a research task
            "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
        },
    )

    try:
        # Server-side fallback: if a safety classifier declines the request, the
        # API re-runs it on another model inside the same call instead of the
        # message being stuck in the queue forever.
        response = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except anthropic.BadRequestError:
        # Older account/model combinations may not have the fallback beta. The
        # extraction matters more than the safety net -- retry without it.
        response = client.messages.create(**kwargs)

    if getattr(response, "stop_reason", None) == "refusal":
        detail = getattr(getattr(response, "stop_details", None), "explanation", "") or ""
        raise RuntimeError("model declined to answer: %s" % detail.strip())

    raw = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(raw), model, raw


# ---------------------------------------------------------------------------
# heuristic fallback
# ---------------------------------------------------------------------------

FOOD_HINTS = ("ate", "eaten", "eating", "had", "breakfast", "lunch", "dinner", "snack",
              "meal", "protein shake", "shake", "coffee", "chicken", "rice", "eggs",
              "oats", "pizza", "salad", "steak", "pasta", "yoghurt", "yogurt")
WORKOUT_HINTS = ("workout", "worked out", "gym", "trained", "training", "session",
                 "lifted", "ran", "run", "running", "cycled", "swim", "legs", "push",
                 "pull", "chest", "back day", "squat", "bench", "deadlift", "cardio")
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
FOCUS_WORDS = ("legs", "push", "pull", "chest", "back", "shoulders", "arms", "core",
               "full body", "cardio")

_NUMBER = r"(\d+(?:[.,]\d+)?)"


def _has_hint(text: str, hints) -> bool:
    """Whole-word hint matching. Substring matching looks fine until "creatine"
    contains "ate" and a supplement becomes a phantom meal."""
    return any(re.search(r"\b%s\b" % re.escape(h), text) for h in hints)


def _f(match: Optional[re.Match], group: int = 1) -> Optional[float]:
    if not match:
        return None
    try:
        return float(match.group(group).replace(",", "."))
    except (TypeError, ValueError):
        return None


def heuristic_payload(text: str) -> Dict[str, Any]:
    """Keyword extraction. Coarse on purpose -- see the module docstring."""
    low = " " + text.lower().strip() + " "
    payload: Dict[str, Any] = {k: [] for k in
                               ("meals", "workouts", "body_metrics", "sleep", "hydration",
                                "supplements", "symptoms", "notes")}
    payload["summary"] = ""

    weight = _f(re.search(_NUMBER + r"\s*(?:kg|kilos?|kilograms?)\b", low))
    if weight is None and re.search(r"weigh(?:ed|s|t)?\b", low):
        weight = _f(re.search(r"weigh(?:ed|s|t)?\s*(?:in at\s*)?" + _NUMBER, low))
    if weight is not None and (re.search(r"weigh|scale|bodyweight", low)
                               or not re.search(r"x\s*\d|reps|sets", low)):
        payload["body_metrics"].append(
            {"when": None, "weight_kg": weight, "body_fat_pct": None,
             "waist_cm": None, "resting_hr": None})

    hours = _f(re.search(r"slept\s*(?:for\s*)?" + _NUMBER, low)) or \
        _f(re.search(_NUMBER + r"\s*(?:h|hrs?|hours)\s*(?:of\s*)?sleep", low))
    if hours is not None:
        quality = "poor" if re.search(r"rough|bad|terrible|awful", low) else None
        payload["sleep"].append({"when": None, "hours": hours, "quality": quality,
                                 "notes": None})

    litres = _f(re.search(_NUMBER + r"\s*(?:l|liters?|litres?)\b", low))
    millis = _f(re.search(_NUMBER + r"\s*ml\b", low))
    if re.search(r"water|drank|hydrat", low):
        ml = millis if millis is not None else (litres * 1000 if litres is not None else None)
        if ml is not None:
            payload["hydration"].append({"when": None, "ml": ml, "drink": "water"})

    if _has_hint(low, WORKOUT_HINTS):
        duration = _f(re.search(_NUMBER + r"\s*(?:min|mins|minutes)\b", low))
        if duration is None and re.search(r"\b(short|quick)\b", low):
            duration = 20.0
        focus = next((w for w in FOCUS_WORDS if w in low), None)
        sets: List[Dict[str, Any]] = []
        for exercise, reps_spec in re.findall(
                r"([a-z][a-z ]{2,20}?)\s*(\d+\s*[x×]\s*\d+)", low):
            n_sets, reps = re.split(r"[x×]", reps_spec)
            sets.append({"exercise": exercise.strip(), "set_no": int(n_sets.strip()),
                         "reps": float(reps.strip()), "weight_kg": None, "rpe": None,
                         "distance_km": None, "duration_s": None})
        payload["workouts"].append({
            "when": None, "kind": "cardio" if re.search(r"\bran\b|\brun\b|cycl|swim|cardio", low)
            else "strength", "focus": focus, "duration_min": duration,
            "intensity": "light" if "short" in low or "easy" in low else None,
            "perceived_effort": None, "calories_est": None, "notes": text.strip(),
            "sets": sets, "confidence": 0.3,
        })

    if _has_hint(low, FOOD_HINTS):
        meal_type = next((m for m in MEAL_TYPES if m in low), None)
        payload["meals"].append({
            "when": None, "meal_type": meal_type, "description": text.strip(),
            "items": [], "calories": None, "protein_g": None, "carbs_g": None,
            "fat_g": None, "confidence": 0.2,
        })

    if not any(payload[k] for k in ("meals", "workouts", "body_metrics", "sleep",
                                    "hydration")) and text.strip():
        payload["notes"].append({"when": None, "text": text.strip()})

    payload["summary"] = "Logged from keywords only (no model available); macros not estimated."
    return payload


# ---------------------------------------------------------------------------
# driving one message through
# ---------------------------------------------------------------------------

def extract(text: str, message_time: str, tz_name: str = "UTC",
            model: Optional[str] = None,
            use_model: bool = True) -> Tuple[Dict[str, Any], str, Optional[str]]:
    """Structure one message. Falls back to the heuristic if Claude is not
    usable, and says which engine ran so the caller can be honest about it."""
    if use_model:
        try:
            return claude_payload(text, message_time, tz_name, model=model)
        except ImportError:
            pass                     # `pip install anthropic` not done yet
        except Exception as exc:     # noqa: BLE001 - auth, network, quota, refusal
            payload = heuristic_payload(text)
            payload["summary"] = ("Logged with keywords only -- the model call failed (%s)."
                                  % type(exc).__name__)
            return payload, "heuristic", str(exc)
    return heuristic_payload(text), "heuristic", None


def process_message(conn: sqlite3.Connection, msg: Dict[str, Any],
                    model: Optional[str] = None, use_model: bool = True,
                    dry_run: bool = False) -> Dict[str, Any]:
    """Interpret one stored message and write its rows.

    A message is only marked 'processed' after the rows are committed, so a
    crash mid-way leaves it pending and it gets retried rather than lost."""
    body = db.message_body(msg)
    if not body:
        db.set_message_status(conn, msg["id"], "needs_transcription")
        return {"message_id": msg["id"], "status": "needs_transcription", "counts": {}}

    user = conn.execute("SELECT * FROM users WHERE id = ?", (msg["user_id"],)).fetchone()
    tz_name = user["tz"] if user else config.timezone_name()

    payload, engine, error = extract(body, msg["received_at"], tz_name,
                                     model=model, use_model=use_model)
    if dry_run:
        return {"message_id": msg["id"], "engine": engine, "payload": payload,
                "dry_run": True}

    counts = db.write_entries(conn, msg["user_id"], msg["id"], payload,
                              default_ts=msg["received_at"])
    db.log_extraction(conn, msg["id"], engine, ok=not error,
                      raw_json=db.dumps(payload), error=error)
    db.set_message_status(conn, msg["id"], "processed")
    return {"message_id": msg["id"], "engine": engine, "counts": counts,
            "summary": payload.get("summary") or "", "status": "processed"}


def process_pending(conn: sqlite3.Connection, limit: int = 25,
                    model: Optional[str] = None, use_model: bool = True,
                    dry_run: bool = False,
                    notify=None) -> List[Dict[str, Any]]:
    """Work the queue. `notify(chat_id, text)` is called per message when given,
    which is how the detailed breakdown gets back to Telegram."""
    from . import report

    out = []
    for msg in db.pending_messages(conn, limit=limit):
        try:
            result = process_message(conn, msg, model=model, use_model=use_model,
                                     dry_run=dry_run)
        except Exception as exc:                       # noqa: BLE001
            db.log_extraction(conn, msg["id"], "unknown", ok=False, error=str(exc))
            db.set_message_status(conn, msg["id"], "failed", error=str(exc))
            out.append({"message_id": msg["id"], "status": "failed", "error": str(exc)})
            continue
        out.append(result)
        if notify and not dry_run and result.get("counts"):
            notify(msg["chat_id"], report.logged_text(result))
    return out
