# QuantLab — free LuxAlgo Quant / Algos / Library / QuantPad alternative

A dependency-free (Python stdlib only) toolkit that replicates the core loop
of LuxAlgo **Quant**, the **Algorithmic Library**, and **QuantPad** at $0:

  • Describe a strategy in plain English  -> the generator picks a strategy + tuning
  • Backtest it                           -> an event-driven engine reports equity,
                                             drawdown, win rate, profit factor, Sharpe,
                                             and an A-F verdict grade
  • Screen all strategies at once         -> `compare` ranks every built-in by Sharpe
  • Stress-test it                        -> walk-forward folds + Monte Carlo resampling
  • Export to Pine Script                 -> copy-paste into TradingView
  • Pull free market data                 -> Yahoo / Stooq / Binance (no API key)

No numpy, no pandas, no pip install. Runs on any Python 3.8+.

────────────────────────────────────────────────────────────────────
WHAT THIS IS (and isn't)
────────────────────────────────────────────────────────────────────
This is the OPEN equivalent of the paid products. It does NOT include:
  • LuxAlgo's proprietary trained model, screeners, or alerts
  • LuxAlgo Library's SMC/ICT/Wyckoff/ML pattern-detection indicators
  • QuantPad's licensed institutional data (CME/FRED/EDGAR), Monte Carlo
    across thousands of paths with regime-switching, or prop-firm rule sims

It DOES give you the same workflow: idea -> code -> backtest -> validate
robustness -> Pine export, plus free data adapters that work on your machine.

────────────────────────────────────────────────────────────────────
QUICKSTART
────────────────────────────────────────────────────────────────────
# 1) natural-language strategy -> backtest on bundled sample data
python run.py "buy when rsi is below 30 and sell above 70" --data sample --plot

# 2) list built-in strategies (the "Library")
python run.py list

# 3) screen every strategy on the same data, ranked by Sharpe (the "Screener")
python run.py compare --data sample

# 4) stress-test a strategy: out-of-sample folds + trade resampling
python run.py "macd" --data sample --walkforward 4 --montecarlo 1000

# 5) live data (requires internet)
python run.py "sma crossover 20 50" --data yahoo --symbol SPY
python run.py "bollinger breakout 20 2.5" --data stooq --symbol aapl.us
python run.py "20 bar breakout" --data binance --symbol BTCUSDT

# 6) tune capital / fees
python run.py "atr trend 50" --data sample --capital 5000 --fee 2

────────────────────────────────────────────────────────────────────
DATA SOURCES (all free, no key)
────────────────────────────────────────────────────────────────────
  yahoo   -> query1.finance.yahoo.com   (stocks/ETF/crypto/fx)
  stooq   -> stooq.com                  (stocks/indices/fx)
  binance -> api.binance.com            (crypto)
  sample  -> bundled synthetic series   (offline, always works)

────────────────────────────────────────────────────────────────────
BUILT-IN STRATEGY LIBRARY
────────────────────────────────────────────────────────────────────
  sma         SMA Crossover
  rsi         RSI Mean Reversion
  boll        Bollinger Breakout
  atr         ATR Trend Filter
  donchian    N-Bar Breakout (Donchian)
  macd        MACD Crossover
  stoch       Stochastic Reversion
  vwap        Rolling VWAP Trend
  supertrend  Supertrend Follow
  roc         Momentum ROC
  lorentzian  Lorentzian Classification (ML/KNN, port of jdehorty's public indicator)

Run `python run.py list` for live descriptions.

────────────────────────────────────────────────────────────────────
VALIDATION TOOLS
────────────────────────────────────────────────────────────────────
  Verdict grade   every backtest report grades itself A-F on edge
                  (Sharpe), robustness (profit factor), risk (max
                  drawdown), and sample size (trade count).

  --walkforward K splits the series into K contiguous out-of-sample
                  folds and re-runs the same fixed strategy+params on
                  each, so you can see whether an edge holds up across
                  different periods instead of being an artifact of
                  the full-period fit.

  --montecarlo N  bootstrap-resamples the realized trade sequence N
                  times to show the P5/P50/P95 range of outcomes --
                  how much of the result could be sequence luck.

────────────────────────────────────────────────────────────────────
PROJECT LAYOUT
────────────────────────────────────────────────────────────────────
  engine.py      backtest engine + indicators (SMA/EMA/RSI/ATR/Bollinger/
                 Donchian/MACD/Stochastic/VWAP/Supertrend/ROC) + verdict scorer
  strategies.py  built-in strategies mirroring public TA concepts
  gen.py         plain-English -> strategy router (the "Quant" equivalent)
  data.py        free data adapters (Yahoo / Stooq / Binance / sample)
  run.py         CLI entry point (single backtest / compare / walk-forward / Monte Carlo)
  test_engine.py assert-based self-check (`python test_engine.py`)

────────────────────────────────────────────────────────────────────
ADDING YOUR OWN STRATEGY
────────────────────────────────────────────────────────────────────
Subclass `engine.Strategy`, implement `prepare()` and `decide(i)` (return
"LONG"/"FLAT"/"SHORT"), then register it in `strategies.REGISTRY`.

────────────────────────────────────────────────────────────────────
DISCLAIMER
────────────────────────────────────────────────────────────────────
Educational / research only. Backtests on random or historical data are
not predictive of future returns. Not financial advice.
