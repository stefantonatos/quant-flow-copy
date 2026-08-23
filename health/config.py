"""
health/config.py - environment-driven settings for the Telegram health chat.

Nothing here has a hardcoded secret. Everything comes from the environment, so
the same code runs on a laptop, a Raspberry Pi, or a VPS with no edits.

Required for the Telegram side:
  TELEGRAM_BOT_TOKEN        from @BotFather
  TELEGRAM_ALLOWED_CHATS    comma-separated chat ids that may write to the db.
                            Leave unset ONLY while testing -- an open bot means
                            anyone who finds it can put rows in your health log.

Optional:
  HEALTH_DB                 sqlite path        (default: <repo>/health.db)
  HEALTH_TZ                 IANA zone name     (default: UTC)
  HEALTH_MODEL              Claude model id    (default: claude-opus-5)
  HEALTH_MEDIA_DIR          voice download dir (default: <repo>/health_media)
  HEALTH_TRANSCRIBE_CMD     shell command that turns an audio file into text on
                            stdout, with {path} substituted. Unset means voice
                            messages are stored but not interpreted.
  ANTHROPIC_API_KEY         read by the Anthropic SDK itself, not by this module
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

DEFAULT_MODEL = "claude-opus-5"


def db_path() -> str:
    return os.environ.get("HEALTH_DB") or os.path.join(REPO, "health.db")


def media_dir() -> str:
    return os.environ.get("HEALTH_MEDIA_DIR") or os.path.join(REPO, "health_media")


def bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Talk to @BotFather in Telegram, "
            "create a bot, and export the token it gives you."
        )
    return token


def allowed_chats() -> set:
    """Chat ids permitted to write. Empty set means 'allow everyone', which is
    only ever appropriate while you are testing on a bot nobody else knows."""
    raw = os.environ.get("TELEGRAM_ALLOWED_CHATS", "").strip()
    if not raw:
        return set()
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            out.add(int(part))
    return out


def timezone_name() -> str:
    return os.environ.get("HEALTH_TZ", "UTC")


def model() -> str:
    return os.environ.get("HEALTH_MODEL", DEFAULT_MODEL)


def transcribe_cmd() -> str:
    return os.environ.get("HEALTH_TRANSCRIBE_CMD", "").strip()
