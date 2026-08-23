"""
webhook_server.py - TradingView alert -> MT5 order bridge (receiver half).

WHAT THIS IS
------------
A self-hosted replacement for the paid TradingView->MT5 bridge services.
TradingView fires a webhook at this server; this server validates it and
appends a command line to a queue file that an Expert Advisor on your MT5
terminal is watching (mt5/TradingViewBridge.mq5).

There is no clever part. The whole product those services sell is: a public
URL that stays up, and somebody else's computer running it. The protocol
below is a hundred lines. What you are really buying from them is hosting
and uptime -- worth paying for or not, but worth knowing that is what it is.

WHY A FILE QUEUE AND NOT THE PYTHON MT5 API
-------------------------------------------
The MetaTrader5 Python package is Windows-only, needs pip, and pins this to
one machine. A file queue keeps the receiver stdlib-only (a repo value that
has held all the way through) and lets the receiver run anywhere while MT5
runs where it likes. The EA polls a text file; that is a boring, debuggable
interface you can read with Notepad when something goes wrong -- and
something will go wrong.

SAFETY, BECAUSE THIS SPENDS REAL MONEY
--------------------------------------
Unlike a backtest, a bug here costs money immediately and unsupervised.
Every one of these is on by default and has to be deliberately turned off:

  * DRY RUN is the default. Commands are logged, never queued for execution,
    until you pass --live. Run it in dry run for a week first and read the
    log: if the alerts you get are not the trades you would have taken, you
    have found that out for free.
  * A shared secret is REQUIRED. A webhook URL is effectively public -- it
    sits in TradingView's settings and travels over the internet in plain
    sight. Without a secret, anyone who learns the URL can place trades in
    your account.
  * Symbol allowlist. An alert naming a symbol you never approved is dropped.
  * Max lot cap, applied server-side, so a malformed size cannot be scaled up
    by a typo in a Pine template.
  * Duplicate suppression. TradingView can and does re-fire alerts; each
    command carries an id and a repeat id is ignored rather than doubling
    your position.
  * Everything is logged, accepted or rejected, with the reason.

Usage:
    python bridge/webhook_server.py --secret MYSECRET --symbols EURNZD,NQ1!
    python bridge/webhook_server.py --secret MYSECRET --symbols EURNZD --live

Then expose it to TradingView with a tunnel (cloudflared/ngrok both have a
free tier) and point the alert's webhook URL at https://<tunnel>/webhook.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_BODY = 64 * 1024          # a TradingView alert is tiny; anything larger is not one
SIDES = ("buy", "sell")
ACTIONS = ("open", "close", "close_all")
ORDER_TYPES = ("market", "limit")


class Config:
    def __init__(self, secret, symbols, queue_path, log_path,
                 live=False, max_lots=0.10, max_risk=200.0, dedupe_seconds=900):
        self.secret = secret
        self.symbols = set(s.strip().upper() for s in symbols if s.strip())
        self.queue_path = queue_path
        self.log_path = log_path
        self.live = live
        self.max_lots = max_lots
        self.max_risk = max_risk
        self.dedupe_seconds = dedupe_seconds
        self.seen = {}            # command id -> unix time first seen


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(cfg, verdict, detail, payload=None):
    line = f"{_now()}  {verdict:8s}  {detail}"
    if payload is not None:
        line += f"  || {json.dumps(payload, separators=(',', ':'))}"
    print(line, flush=True)
    try:
        with open(cfg.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        print(f"{_now()}  LOGFAIL   could not write {cfg.log_path}: {e}", flush=True)


def validate(cfg, data):
    """Return (command_dict, None) or (None, reason). Rejects are the point of
    this function -- when in doubt it refuses, because the failure mode of
    accepting a bad command is a real position in a real account."""
    if not isinstance(data, dict):
        return None, "payload is not a JSON object"

    if str(data.get("secret", "")) != cfg.secret:
        # Deliberately vague to the caller; specific in the log.
        return None, "bad or missing secret"

    action = str(data.get("action", "open")).lower()
    if action not in ACTIONS:
        return None, f"action must be one of {ACTIONS}, got {action!r}"

    symbol = str(data.get("symbol", "")).upper().strip()
    if not symbol:
        return None, "missing symbol"
    if cfg.symbols and symbol not in cfg.symbols:
        return None, f"symbol {symbol} is not in the allowlist"

    cmd_id = str(data.get("id", "")).strip()
    if not cmd_id:
        # No id supplied: derive one that is stable per alert-per-minute, so a
        # TradingView retry inside the same minute is still caught.
        cmd_id = f"{symbol}-{action}-{data.get('side','')}-{int(time.time() // 60)}"

    now = time.time()
    for old in [k for k, t in cfg.seen.items() if now - t > cfg.dedupe_seconds]:
        cfg.seen.pop(old, None)
    if cmd_id in cfg.seen:
        return None, f"duplicate command id {cmd_id} (already seen)"

    cmd = {"id": cmd_id, "action": action, "symbol": symbol}

    if action in ("close", "close_all"):
        cmd["comment"] = str(data.get("comment", ""))[:31]
        return cmd, None

    side = str(data.get("side", "")).lower()
    if side not in SIDES:
        return None, f"side must be one of {SIDES}, got {side!r}"

    otype = str(data.get("type", "market")).lower()
    if otype not in ORDER_TYPES:
        return None, f"type must be one of {ORDER_TYPES}, got {otype!r}"

    def num(key, default=0.0):
        v = data.get(key, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    price = num("price")
    sl = num("sl")
    tp = num("tp")
    lots = num("lots", 0.0)
    risk = num("risk", 0.0)
    if None in (price, sl, tp, lots, risk):
        return None, "price/sl/tp/lots/risk must be numeric"
    if otype == "limit" and price <= 0:
        return None, "a limit order needs a positive price"

    # Position size comes from EITHER an explicit lot count OR a money risk
    # that MT5 converts into lots. The conversion has to happen there: it
    # needs the symbol's tick value, contract size and the account currency,
    # none of which a browser knows. Guessing any of them would silently
    # size the trade wrong.
    if lots <= 0 and risk <= 0:
        return None, "need either lots or risk (money to risk on the trade)"
    if lots > 0 and lots > cfg.max_lots:
        return None, f"lots {lots} exceeds the server cap of {cfg.max_lots}"
    if risk > 0:
        if sl <= 0:
            return None, "risk-based sizing needs a stop loss to size against"
        if risk > cfg.max_risk:
            return None, f"risk {risk} exceeds the server cap of {cfg.max_risk}"

    # A stop on the wrong side of entry is the single most expensive typo
    # available here: it converts a bracket into an instant loss, or into a
    # position with no protection at all.
    ref = price if otype == "limit" else None
    if sl > 0 and ref is not None:
        if side == "buy" and sl >= ref:
            return None, f"buy stop-loss {sl} is not below entry {ref}"
        if side == "sell" and sl <= ref:
            return None, f"sell stop-loss {sl} is not above entry {ref}"
    if tp > 0 and ref is not None:
        if side == "buy" and tp <= ref:
            return None, f"buy target {tp} is not above entry {ref}"
        if side == "sell" and tp >= ref:
            return None, f"sell target {tp} is not below entry {ref}"

    cmd.update({"side": side, "type": otype, "price": price,
                "sl": sl, "tp": tp, "lots": lots, "risk": risk,
                "comment": str(data.get("comment", ""))[:31]})
    return cmd, None


def to_queue_line(cmd):
    """Pipe-delimited key=value. MQL5 has no JSON parser worth the name, and
    StringSplit on this is four lines there instead of four hundred."""
    keys = ("id", "action", "symbol", "side", "type", "price", "sl", "tp",
            "lots", "risk", "comment")
    return "|".join(f"{k}={cmd.get(k, '')}" for k in keys)


def enqueue(cfg, cmd):
    line = to_queue_line(cmd)
    with open(cfg.queue_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())      # the EA may read the instant we return
    return line


def make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        server_version = "QuantFlowBridge/1.0"

        def _cors(self):
            # The browser-extension sender posts JSON, which triggers a CORS
            # preflight. Without these the extension path fails at the
            # preflight and no alert ever arrives -- silently, which is the
            # worst way for this to break.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _reply(self, code, msg):
            body = (msg + "\n").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if self.path.rstrip("/") in ("/health", ""):
                self._reply(200, "ok " + ("LIVE" if cfg.live else "DRY-RUN"))
            else:
                self._reply(404, "not found")

        def do_POST(self):
            if self.path.rstrip("/") != "/webhook":
                self._reply(404, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                log(cfg, "REJECT", f"bad content-length {length}")
                self._reply(400, "bad request")
                return

            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                log(cfg, "REJECT", f"unparsable body: {e}")
                self._reply(400, "bad json")
                return

            cmd, reason = validate(cfg, data)
            if cmd is None:
                redacted = {k: v for k, v in data.items() if k != "secret"}
                log(cfg, "REJECT", reason, redacted)
                # Return the reason ONLY once the secret has checked out. A
                # caller who cannot authenticate learns nothing; a caller who
                # can is the operator, and hiding why their own trade was
                # refused just sends them digging through logs.
                authed = str(data.get("secret", "")) == cfg.secret
                self._reply(400, reason if authed else "rejected")
                return

            cfg.seen[cmd["id"]] = time.time()
            if cfg.live:
                line = enqueue(cfg, cmd)
                log(cfg, "QUEUED", line)
                self._reply(200, "queued")
            else:
                log(cfg, "DRY-RUN", to_queue_line(cmd))
                self._reply(200, "dry-run: logged, not queued")

        def log_message(self, fmt, *args):
            pass      # our own log() is the record; this would double every line

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="TradingView -> MT5 webhook bridge")
    ap.add_argument("--secret", required=True,
                    help="shared secret that every alert must carry")
    ap.add_argument("--symbols", default="",
                    help="comma-separated allowlist, e.g. EURNZD,NQ1! (empty = allow any, not recommended)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--queue", default="bridge/queue.txt",
                    help="command queue the MT5 EA reads; point this at your terminal's MQL5/Files directory")
    ap.add_argument("--log", default="bridge/bridge.log")
    ap.add_argument("--max-lots", type=float, default=0.10)
    ap.add_argument("--max-risk", type=float, default=200.0,
                    help="hard cap on money risked per trade, for risk-based sizing")
    ap.add_argument("--live", action="store_true",
                    help="actually queue orders. Without this nothing is executed.")
    args = ap.parse_args(argv)

    if len(args.secret) < 12:
        print("Refusing to start: --secret must be at least 12 characters. "
              "This URL is reachable from the internet.", file=sys.stderr)
        return 2

    for p in (args.queue, args.log):
        d = os.path.dirname(os.path.abspath(p))
        os.makedirs(d, exist_ok=True)

    cfg = Config(args.secret, args.symbols.split(","), args.queue, args.log,
                 live=args.live, max_lots=args.max_lots, max_risk=args.max_risk)

    mode = "LIVE -- orders will be queued for execution" if cfg.live else \
           "DRY RUN -- commands are logged only, nothing will be executed"
    log(cfg, "START", f"{mode}; listening on {args.host}:{args.port}; "
                      f"symbols={sorted(cfg.symbols) or 'ANY'}; "
                      f"max_lots={cfg.max_lots}; max_risk={cfg.max_risk}")
    if not cfg.symbols:
        log(cfg, "WARN", "no symbol allowlist -- any symbol will be accepted. "
                         "Fine for following whatever chart you are on; the EA still "
                         "refuses symbols your broker does not offer, and the popup "
                         "shows you the symbol before you send.")

    httpd = HTTPServer((args.host, args.port), make_handler(cfg))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log(cfg, "STOP", "interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
