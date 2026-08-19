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

## Quick start (Windows)

Double-click **`start_bridge.bat`** in the repo root. It starts the server,
opens your chart in Brave, and launches MT5. Edit the settings block at the top
of that file once — secret, symbols, your MT5 queue path, and which chart to
open — and after that it is one click.

It refuses to start silently when something is wrong: no Python, no bridge
folder, or an MT5 queue path that does not exist. That last check matters more
than it looks — a wrong queue path means the server cheerfully writes orders
into a folder MT5 never reads, and nothing appears broken until you notice no
trades are being placed.

Make a desktop shortcut: right-click `start_bridge.bat` → Show more options →
Send to → Desktop.

## Setup

**1. Start the receiver** (leave dry-run on to begin with — no `--live`):

```
python bridge/webhook_server.py --secret PICK-SOMETHING-LONG --symbols EURNZD
```

**2. Choose how trades reach it.** Which route you can use depends on your
TradingView plan, and the free plan is more restricted than it looks:

> **TradingView free (Basic) gives you 3 PRICE alerts and ZERO technical
> alerts.** Anything driven by a Pine script — indicator, strategy, `alert()`
> — is a technical alert. So on a free account **no script in this repo can
> ever fire an alert**, and both alert-based routes are unavailable. This is
> exactly why the commercial bridges ship an indicator plus a browser
> extension instead of using alerts.

| Route | Plan needed | How it triggers | Browser open? |
|---|---|---|---|
| **Indicator + popup** | **Any, incl. free** | broker fills a pending limit order | only to send it |
| Extension watches alerts | Paid (technical alerts) | alert fires on the page | yes, always |
| Webhook | Paid | TradingView posts server-side | no |

**Indicator + popup — the free-plan route, and the one to use.**

1. Add `pine/bridge_levels.pine` to your chart.
2. Set the entry, stop and target — type them in, or set *Levels from* to
   `nowick` / `asia` and it fills them from that setup.
3. Click the extension → **Read levels from chart** → check the numbers →
   **Send to MT5**.

That sends a **pending limit order**. Your broker holds it and fills it when
price reaches your entry — on their servers, whether or not your browser is
open, your PC is awake, or this extension is still running.

This is deliberately not a copy of how the paid services do it. They keep a
browser tab watching price and fire a market order on touch, which needs the
tab alive at the exact moment it matters and adds browser latency to the fill.
A resting limit order gets the same result with fewer things that can be
switched off at the wrong moment — and it is what limit orders are for.

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

## Position sizing

Set **Risk ($)** in the popup and leave the lot box alone. MT5 works out the
lot size from your risk and your stop distance, using this broker's real
contract specs for the symbol — tick value, contract size, account currency.
That calculation cannot be done correctly in a browser, which knows none of
those things.

**Rounding is always DOWN**, and if the risk works out to less than the
symbol's minimum lot, the EA **refuses the trade** and prints what the minimum
would actually have risked. Trading the minimum "to be helpful" would risk
more than you asked for, silently.

⚠️ **`MaxLots` on the EA has to be big enough for the risk you want.** These
interact in a way that is easy to miss: a tight stop needs a *large* lot size
to risk a given amount. $100 over a 10-pip stop on EURNZD is roughly **1.7
lots** — which the default `MaxLots = 0.10` will reject. Work out the biggest
size your normal stop distance implies and set `MaxLots` a little above it.
Keep it finite: it is the backstop against a malformed price producing an
enormous position.

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
