"""
health/telegram.py - a small Telegram Bot API client, stdlib only.

There is no dependency here on purpose: the Bot API is JSON over HTTPS and
`urllib` covers it. Only the handful of methods this project needs are
implemented -- getUpdates, sendMessage, getFile, and file download.

Long polling, not webhooks. A webhook needs a public HTTPS endpoint; long
polling runs from a laptop behind NAT, which is where this will actually live.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

API_ROOT = "https://api.telegram.org"

# Telegram's own cap on the reply text. Longer messages are rejected outright,
# so the coach truncates rather than losing the whole send.
MAX_TEXT = 4096


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    """Thin wrapper over the Bot API.

    `transport` exists for tests: pass a callable taking (method, params) and
    returning the decoded 'result' payload, and no HTTP happens at all."""

    def __init__(self, token: str, timeout: float = 65.0,
                 transport: Optional[Callable[[str, Dict[str, Any]], Any]] = None):
        self.token = token
        self.timeout = timeout
        self.transport = transport

    # -- plumbing ----------------------------------------------------------

    def _url(self, method: str) -> str:
        return "%s/bot%s/%s" % (API_ROOT, self.token, method)

    def call(self, method: str, params: Optional[Dict[str, Any]] = None,
             retries: int = 3) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if self.transport is not None:
            return self.transport(method, params)

        body = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            self._url(method), data=body,
            headers={"Content-Type": "application/json"},
        )
        delay = 2.0
        last: Optional[Exception] = None
        for _ in range(max(1, retries)):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if not payload.get("ok"):
                    raise TelegramError("%s failed: %s" % (method, payload.get("description")))
                return payload.get("result")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                # 4xx other than 429 is our bug -- a bad token or bad arguments.
                # Retrying it just burns time.
                if exc.code != 429 and 400 <= exc.code < 500:
                    raise TelegramError("%s HTTP %d: %s" % (method, exc.code, detail))
                last = TelegramError("%s HTTP %d: %s" % (method, exc.code, detail))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            time.sleep(delay)
            delay *= 2
        raise TelegramError("%s failed after retries: %s" % (method, last))

    # -- methods -----------------------------------------------------------

    def get_me(self) -> Dict[str, Any]:
        return self.call("getMe")

    def get_updates(self, offset: Optional[int] = None, timeout: int = 30,
                    limit: int = 50) -> List[Dict[str, Any]]:
        """Long-poll for updates. `offset` must be last_update_id + 1; sending it
        is what tells Telegram to stop redelivering everything before it."""
        return self.call("getUpdates", {
            "offset": offset, "timeout": timeout, "limit": limit,
            "allowed_updates": ["message"],
        }) or []

    def send_message(self, chat_id: int, text: str,
                     reply_to: Optional[int] = None) -> Dict[str, Any]:
        """Send plain text. No parse_mode on purpose: food and exercise names are
        full of underscores and asterisks, and Markdown parsing turns a logged
        meal into a 400."""
        if len(text) > MAX_TEXT:
            text = text[: MAX_TEXT - 3] + "..."
        return self.call("sendMessage", {
            "chat_id": chat_id, "text": text,
            "reply_to_message_id": reply_to,
            "disable_notification": False,
        })

    def get_file_path(self, file_id: str) -> str:
        info = self.call("getFile", {"file_id": file_id})
        path = (info or {}).get("file_path")
        if not path:
            raise TelegramError("getFile returned no file_path for %s" % file_id)
        return path

    def download(self, file_id: str, dest_dir: str) -> str:
        """Download a file (a voice note, in practice) and return the local path."""
        remote = self.get_file_path(file_id)
        os.makedirs(dest_dir, exist_ok=True)
        local = os.path.join(dest_dir, "%s_%s" % (file_id[-16:], os.path.basename(remote)))
        url = "%s/file/bot%s/%s" % (API_ROOT, self.token, urllib.parse.quote(remote))
        with urllib.request.urlopen(url, timeout=self.timeout) as resp, \
                open(local, "wb") as fh:
            fh.write(resp.read())
        return local


def parse_message(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Flatten a Telegram update into the fields the inbox stores.

    Returns None for updates this bot has no use for (edits, channel posts,
    stickers). Voice and audio both map to kind='voice' -- Telegram sends a
    recorded-in-app note as `voice` and a forwarded file as `audio`, but for our
    purposes they are the same thing: sound that needs transcribing."""
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    if not chat.get("id"):
        return None

    out = {
        "update_id": update.get("update_id"),
        "chat_id": int(chat["id"]),
        "telegram_message_id": msg.get("message_id"),
        "telegram_user_id": int(sender.get("id") or chat["id"]),
        "name": (" ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
                 or sender.get("username") or None),
        "date": msg.get("date"),
        "kind": "other",
        "text": None,
        "file_id": None,
        "duration_s": None,
    }

    if msg.get("text"):
        out["kind"] = "text"
        out["text"] = msg["text"]
    elif msg.get("voice") or msg.get("audio"):
        media = msg.get("voice") or msg.get("audio")
        out["kind"] = "voice"
        out["file_id"] = media.get("file_id")
        out["duration_s"] = media.get("duration")
        out["text"] = msg.get("caption")
    elif msg.get("photo"):
        out["kind"] = "photo"
        out["file_id"] = (msg["photo"][-1] or {}).get("file_id")
        out["text"] = msg.get("caption")
    elif msg.get("caption"):
        out["text"] = msg["caption"]
    else:
        return None

    return out
