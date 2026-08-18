# TradingView → MT5 bridge

A self-hosted replacement for the paid TradingView→MT5 bridge services
(ViewLink and similar). Your Pine strategy fires an alert; this catches it and
places the order on your MT5 account.

## What you are actually replacing

Almost none of it is algorithm. A bridge like this is a webhook receiver plus
an order placer — the two files here, about 500 lines including comments and
safety checks. **What those services really sell is hosting**: a URL that stays
up whether or not your PC does. That is a real service and may well be worth
paying for. It is just worth knowing that is what you are buying, rather than
any secret.

The one thing you give up self-hosting: your machine has to be on and online
when an alert fires. If it is asleep, the trade does not happen.

## Pieces

| File | Runs where | Job |
|---|---|---|
| `bridge/webhook_server.py` | your PC (or any host) | receives the alert, validates it, appends a command line |
| `mt5/TradingViewBridge.mq5` | MT5 terminal | reads those lines, places the orders |

They talk through a plain text file. That is deliberate: you can open it in
Notepad mid-run, see exactly what was asked for, and delete a line to cancel
it. Nothing about a socket would be better here, and a lot would be worse.

## Setup

**1. Start the receiver** (leave dry-run on to begin with — no `--live`):

```
python bridge/webhook_server.py --secret PICK-SOMETHING-LONG --symbols EURNZD
```

**2. Choose how alerts reach it.** Two routes, and which one you can use
depends on your TradingView plan:

| | Webhook | Chrome extension |
|---|---|---|
| TradingView plan | **Paid only** (free has no webhook box) | Any, including free |
| Needs browser open | No | **Yes** — tab open, on the chart, awake |
| Needs a public URL | Yes (free tunnel) | No |
| Breaks when TradingView redesigns | No | **Yes**, and probably quietly |

**Webhook route (preferred if you have a paid plan).** Give the server a public
URL: `cloudflared tunnel --url http://localhost:8787` or `ngrok http 8787`.
Both print an `https://…` address; your webhook URL is that plus `/webhook`.

**Extension route (works on the free plan).** Chrome → Extensions →
Extension. Set the endpoint (`http://127.0.0.1:8787/webhook`) and the same
secret, then press **Send test alert** — it should come back `200 queued`, and
you should see the line appear in the queue file. The extension reads the alert
text off the TradingView page and posts it locally, which is precisely why it
needs no paid plan and no public URL.

This is the honest trade: the extension costs you nothing and works on any
plan, but it only fires while the tab is open, and it depends on TradingView's
HTML not changing. The webhook costs a subscription and has neither problem.

**3. Install the EA.** MetaEditor → open `mt5/TradingViewBridge.mq5` → Compile
(F7) → drag onto any chart → enable AutoTrading. Point the server's `--queue`
at your terminal's `MQL5/Files` folder (MT5 → File → Open Data Folder), because
MT5 will not read files outside it.

**4. Set the alert in TradingView.** Condition: *Any alert() function call*.
Paste the JSON below into the message box and tick Webhook URL.

## Alert message template

```json
{
  "secret": "PICK-SOMETHING-LONG",
  "id": "{{timenow}}",
  "action": "open",
  "symbol": "EURNZD",
  "side": "buy",
  "type": "limit",
  "price": {{close}},
  "sl": 1.9550,
  "tp": 1.9700,
  "lots": 0.02,
  "comment": "nowick"
}
```

`{{close}}` and `{{timenow}}` are TradingView placeholders it substitutes at
fire time. For a strategy that already computes its own stop and target, emit
those from Pine instead of hardcoding them.

## The safety rails, and why each exists

Both halves refuse independently. That is on purpose — the EA does not trust
the server, because the EA is the half that spends money.

- **Dry run is the default in both.** Nothing reaches your broker until you
  pass `--live` *and* set `DryRun=false` on the EA. Run it dry for a week and
  read the log: if the alerts are not the trades you would have taken by hand,
  you have learned that for free.
- **Shared secret, minimum 12 characters, enforced at startup.** A webhook URL
  sits in TradingView's settings and travels the open internet. Without a
  secret, anyone who learns it can trade your account.
- **Symbol allowlist.** An alert naming a symbol you never approved is dropped.
- **Two independent lot caps**, server and EA.
- **Stop-direction check.** A buy whose stop is above entry is refused. That
  single typo turns a bracket into an instant loss or an unprotected position,
  and it is the most expensive mistake available in this format.
- **Duplicate suppression.** TradingView re-fires alerts; a repeated id is
  ignored rather than doubling your position.
- **The EA only closes positions it opened** (matched by magic number), so it
  can never touch a trade you placed by hand.
- **Everything is logged** — accepted and rejected, with the reason.

## Before you turn this on

Nothing in this repo has been validated against real market data yet. A bridge
does not create edge; it removes the delay between a signal and a position. If
the strategy loses money, this makes it lose money faster and while you are not
watching.

Measure first. Then automate.

## Tests

```
python bridge/test_bridge.py
```

21 tests, all of them about what the validator must **refuse**.
