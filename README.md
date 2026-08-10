# QuantLab — free LuxAlgo Quant / QuantPad alternative

A dependency-free (Python stdlib only) toolkit that replicates the core loop
of LuxAlgo **Quant** and **QuantPad** at $0:

  • Describe a strategy in plain English  -> the generator picks a strategy + tuning
  • Backtest it                           -> an event-driven engine reports equity,
                                             drawdown, win rate, profit factor, Sharpe
  • Export to Pine Script                 -> copy-paste into TradingView
  • Pull free market data                 -> Yahoo / Stooq / Binance (no API key)

No numpy, no pandas, no pip install. Runs on any Python 3.8+.

────────────────────────────────────────────────────────────────────
WHAT THIS IS (and isn't)
────────────────────────────────────────────────────────────────────
This is the OPEN equivalent of the paid products. It does NOT include:
  • LuxAlgo's proprietary trained model (Quant)
  • QuantPad's licensed institutional data feeds (CME/FRED/EDGAR)

It DOES give you the same workflow: idea -> code -> backtest -> Pine export,
plus free data adapters that work on your machine.

────────────────────────────────────────────────────────────────────
QUICKSTART
────────────────────────────────────────────────────────────────────
# 1) natural-language strategy -> backtest on bundled sample data
python run.py "buy when rsi is below 30 and sell above 70" --data sample --plot

# 2) list built-in strategies
python run.py list

# 3) live data (requires internet)
python run.py "sma crossover 20 50" --data yahoo --symbol SPY
python run.py "bollinger breakout 20 2.5" --data stooq --symbol aapl.us
python run.py "20 bar breakout" --data binance --symbol BTCUSDT

# 4) tune capital / fees
python run.py "atr trend 50" --data sample --capital 5000 --fee 2

────────────────────────────────────────────────────────────────────
DATA SOURCES (all free, no key)
────────────────────────────────────────────────────────────────────
  yahoo   -> query1.finance.yahoo.com   (stocks/ETF/crypto/fx)
  stooq   -> stooq.com                  (stocks/indices/fx)
  binance -> api.binance.com            (crypto)
  sample  -> bundled synthetic series   (offline, always works)

────────────────────────────────────────────────────────────────────
PROJECT LAYOUT
────────────────────────────────────────────────────────────────────
  engine.py      backtest engine + indicators (SMA/EMA/RSI/ATR/Bollinger/Donchian)
  strategies.py  built-in strategies mirroring public TA concepts
  gen.py         plain-English -> strategy router (the "Quant" equivalent)
  data.py        free data adapters (Yahoo / Stooq / Binance / sample)
  run.py         CLI entry point
  .env.example   copy to .env, add your OpenRouter key (gitignored)

────────────────────────────────────────────────────────────────────
ADDING YOUR OWN STRATEGY
────────────────────────────────────────────────────────────────────
Subclass `engine.Strategy`, implement `prepare()` and `decide(i)` (return
"LONG"/"FLAT"/"SHORT"), then register it in `strategies.REGISTRY`.

────────────────────────────────────────────────────────────────────
RUN THIS AGENT ON A FREE MODEL (optional)
────────────────────────────────────────────────────────────────────
This repo's tool-use agent runs on Hermes Agent. To power it with a free
model instead of the default:
  1. Get a free OpenRouter key: https://openrouter.ai/keys  (no card)
  2. echo OPENROUTER_API_KEY=... > .env   (already done locally)
  3. In Hermes:  hermes config set model.provider openrouter
                 hermes config set model.name "openai/gpt-oss-20b:free"
Best free model with tool-calling: openai/gpt-oss-20b:free.
(Multimodal free alt: google/gemma-4-26b-a4b-it:free.)

────────────────────────────────────────────────────────────────────
DISCLAIMER
────────────────────────────────────────────────────────────────────
Educational / research only. Backtests on random or historical data are
not predictive of future returns. Not financial advice.
