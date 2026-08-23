# health — a Telegram chat that logs what you eat and train

Text or voice-note your bot ("ate tandoori chicken, short legs workout"), it
confirms, files it into the right table, and can text you back a check-in built
from your own numbers.

Runs on SQLite and the stdlib. The one optional dependency is `anthropic`, used
for two things: turning a sloppy sentence into typed rows, and wording the
check-in. Without it everything still runs on keyword matching — worse, and it
says so in every message rather than pretending otherwise.

## Setup

```bash
# 1. Create the bot: message @BotFather in Telegram, /newbot, copy the token.
export TELEGRAM_BOT_TOKEN=123456:ABC...

# 2. Send your new bot any message, then find out who you are:
python health_bot.py whoami
export TELEGRAM_ALLOWED_CHATS=<the chat id it printed>

# 3. Optional but strongly recommended — without it, no macro estimates:
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# 4. Optional: your timezone, so a late dinner lands on the right day.
export HEALTH_TZ=Europe/Vienna

python health_bot.py init
```

`TELEGRAM_ALLOWED_CHATS` is not optional in practice. A Telegram bot is public
the moment it exists — anyone who guesses the username can message it. With the
whitelist empty, anyone who does can put rows in your health log.

## Running it

Two processes. The listener:

```bash
python health_bot.py ingest --process
```

and the check-in, from cron:

```cron
0 20 * * *  cd /path/to/repo && python health_bot.py coach --kind daily
0  * * * *  cd /path/to/repo && python health_bot.py coach --kind nudge
```

The hourly nudge is safe: it only sends when something specific is true (no
training in four days, a real protein shortfall against a target you set), at
most once a day per kind. Most hours it sends nothing.

## Trying it without a bot token

```bash
python health_bot.py log "ate chicken and rice, 30 min legs, squats 3x8 at 100kg"
python health_bot.py report --days 7
python health_bot.py coach --kind daily --dry-run
```

`log` injects a message as though it had come from Telegram and runs it through
the same pipeline. Add `--no-model` to force the keyword engine, or `--dry-run`
on `process` to see the JSON the model produced without writing any rows.

## How it fits together

```
Telegram  --ingest.py-->  messages  --extract.py-->  meals / workouts / sleep / ...
                            (raw)                              |
                                                          report.py
                                                               |
                                                           coach.py --> Telegram
```

`messages` is append-only and nothing deletes from it. Every interpreted row
carries the `message_id` it came from, and `extraction_runs` keeps the raw model
output, so any number in a report traces back to the sentence you actually said
and the interpretation that produced it.

The split matters more than it looks: a bad model response can produce a wrong
meal row, but it can never cost you the original message. Re-running extraction
over the inbox is always possible.

## Voice notes

The Claude API takes text and images, not audio, and the Telegram Bot API has no
transcription method for ordinary bots. So this project cannot transcribe a
voice note by itself — it shells out to whatever speech-to-text you have:

```bash
export HEALTH_TRANSCRIBE_CMD='whisper-cli -m models/ggml-base.bin -nt -f {path}'
```

Telegram sends voice notes as OGG/Opus. With the variable unset, voice messages
are still stored and confirmed, but land as `needs_transcription` and the bot
tells you it could not read them. Nothing is silently dropped and nothing is
guessed at.

## What it will not do

- It will not treat a meal it could not price as zero calories. Meals with no
  macro estimate are counted separately and every total that includes one is
  labelled a floor, not a sum.
- It will not average over days you did not log. The week report shows the
  unlogged days and says outright that the averages describe your logging rather
  than your eating.
- The coach only sees the numbers in `report.stats()` and is told to say when
  something is missing rather than fill it in. It is not a doctor, does not
  diagnose, and has nothing to sell you.

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | for the Telegram side | from @BotFather |
| `TELEGRAM_ALLOWED_CHATS` | in practice, yes | comma-separated chat ids allowed to write |
| `ANTHROPIC_API_KEY` | for macro estimates | read by the SDK itself |
| `HEALTH_DB` | no | sqlite path (default `./health.db`) |
| `HEALTH_TZ` | no | IANA zone, e.g. `Europe/Vienna` (default UTC) |
| `HEALTH_MODEL` | no | default `claude-opus-5` |
| `HEALTH_MEDIA_DIR` | no | where voice notes are downloaded |
| `HEALTH_TRANSCRIBE_CMD` | no | speech-to-text command, `{path}` substituted |

## Tests

```bash
python test_health.py
```

41 tests, no network and no API key needed — the Telegram client is driven
through a fake transport and extraction runs on the keyword engine.
