"""
health/transcribe.py - voice notes to text.

An honest note about scope: the Claude API takes text and images, not audio, and
the Telegram Bot API has no transcription method for ordinary bots. So there is
no way for this project to transcribe a voice note by itself. What it does
instead is shell out to whatever speech-to-text you already have.

Set HEALTH_TRANSCRIBE_CMD to a command containing {path}, e.g.

    # whisper.cpp
    export HEALTH_TRANSCRIBE_CMD='whisper-cli -m models/ggml-base.bin -nt -f {path}'
    # openai-whisper (pip install openai-whisper), writes to stdout
    export HEALTH_TRANSCRIBE_CMD='whisper {path} --model base --output_format txt -f txt'
    # faster-whisper via a wrapper script of your own
    export HEALTH_TRANSCRIBE_CMD='python my_stt.py {path}'

Telegram voice notes arrive as OGG/Opus. Most tools handle that directly; if
yours does not, put an ffmpeg conversion in the wrapper script.

With the variable unset, voice messages are still stored -- they land with
status 'needs_transcription' and the bot says so rather than pretending it
understood. Nothing is silently dropped.
"""
from __future__ import annotations

import shlex
import subprocess
from typing import Optional

from . import config


def available() -> bool:
    return bool(config.transcribe_cmd())


def transcribe(path: str, timeout: float = 300.0) -> Optional[str]:
    """Run the configured command against an audio file.

    Returns the transcript, or None if no command is configured. Raises
    RuntimeError if the command is configured but fails -- a broken transcriber
    should be visible, not quietly treated as silence."""
    template = config.transcribe_cmd()
    if not template:
        return None

    if "{path}" in template:
        cmd = [part.replace("{path}", path) for part in shlex.split(template)]
    else:
        cmd = shlex.split(template) + [path]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError("HEALTH_TRANSCRIBE_CMD not runnable: %s" % exc)
    except subprocess.TimeoutExpired:
        raise RuntimeError("transcription timed out after %.0fs" % timeout)

    if proc.returncode != 0:
        raise RuntimeError(
            "transcriber exited %d: %s" % (proc.returncode, (proc.stderr or "").strip()[:300])
        )

    text = (proc.stdout or "").strip()
    if not text:
        raise RuntimeError("transcriber produced no text")
    return text
