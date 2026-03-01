# trader-

A 24/7 Python trading bot for Coinbase that uses technical analysis to **buy at support, sell at resistance**, hold while structure continues, and exit on structure rejection.  It learns from its own trade history and adaptively adjusts position sizing and parameters over time.

---

## Features

| Feature | Detail |
|---|---|
| Exchange | Coinbase Advanced Trade API (official `coinbase-advanced-py` SDK) |
| Strategy | Support/resistance trading with market-structure confirmation |
| Entry signal | Price near support + uptrend (HH/HL) + RSI not overbought + minimum RR ratio |
| Hold logic | Holds while trend structure (higher highs / higher lows) is intact |
| Exit signals | Take-profit, stop-loss, bearish structure break (CHoCH), rejection candle at resistance |
| Risk management | ATR-based stop-loss, configurable max position size, reward-to-risk filter |
| Adaptive learning | Persists trade history to JSON; auto-tunes parameters via half-Kelly sizing |
| Dry-run mode | `DRY_RUN=true` simulates orders without touching real funds (default) |

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/ManuelMello-dev/trader-
cd trader-
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and fill in your Coinbase API key / secret and trading preferences.
```

Generate API credentials at <https://www.coinbase.com/settings/api>.  
The key needs **View** and **Trade** permissions for the relevant product.

### 3. Run in dry-run mode (safe default)

```bash
python bot.py
```

The bot logs every decision to stdout.  No real orders are placed while `DRY_RUN=true`.

### 4. Go live

Set `DRY_RUN=false` in `.env` **only** after reviewing the strategy logic and testing thoroughly with dry-run.

---

## Configuration reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `COINBASE_API_KEY` | *(required)* | Coinbase Advanced Trade API key |
| `COINBASE_API_SECRET` | *(required)* | Coinbase Advanced Trade API secret |
| `TRADING_PAIR` | `BTC-USD` | Product ID (e.g. `ETH-USD`, `SOL-USD`) |
| `QUOTE_CURRENCY` | `USD` | Currency used to fund buys |
| `MAX_POSITION_PCT` | `0.10` | Max fraction of account balance per trade |
| `STOP_LOSS_ATR_MULT` | `1.5` | Stop-loss = support − ATR × this multiplier |
| `TAKE_PROFIT_ATR_MULT` | `3.0` | Take-profit fallback when no resistance found |
| `MIN_RR_RATIO` | `2.0` | Minimum reward-to-risk ratio to enter |
| `CANDLE_GRANULARITY` | `ONE_HOUR` | Candle size (`ONE_MINUTE` … `ONE_DAY`) |
| `LOOKBACK_CANDLES` | `200` | Bars of history used for analysis |
| `PIVOT_WINDOW` | `5` | Bars each side for swing-high/low detection |
| `SR_CLUSTER_PCT` | `0.005` | Price tolerance for S/R level clustering (0.5 %) |
| `BOT_INTERVAL_SECONDS` | `300` | How often the main loop runs (seconds) |
| `DRY_RUN` | `true` | `false` to place real orders |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `TRADE_LOG_FILE` | `trades.json` | JSON file for trade history (adaptive learning) |

---

## How the strategy works

```
Every BOT_INTERVAL_SECONDS:
│
├─ Fetch last LOOKBACK_CANDLES OHLCV candles from Coinbase
│
├─ Technical analysis
│   ├─ Swing highs/lows  →  Support & Resistance levels
│   ├─ Market structure  →  UPTREND / DOWNTREND / NEUTRAL
│   ├─ RSI (14)          →  momentum filter
│   └─ ATR (14)          →  stop-loss & take-profit sizing
│
├─ FLAT (no position)
│   ├─ Trend = UPTREND or NEUTRAL?         No  →  HOLD
│   ├─ Price near support level?            No  →  HOLD
│   ├─ RSI < 65?                            No  →  HOLD
│   ├─ Reward/Risk ≥ MIN_RR_RATIO?          No  →  HOLD
│   ├─ Rejection candle or strong support?  No  →  HOLD (if NEUTRAL)
│   └─ All pass                             →   BUY (half-Kelly size)
│
└─ LONG (position open)
    ├─ Price ≤ stop-loss?                   →  SELL (stop)
    ├─ Price ≥ take-profit?                 →  SELL (target)
    ├─ Bearish structure break (CHoCH)?     →  SELL (structure)
    ├─ Rejection candle at resistance?      →  SELL (rejection)
    └─ None of the above                    →  HOLD
```

---

## Adaptive learning

After each closed trade the bot:
1. Appends a record (entry/exit price, P&L %, reason, timestamp) to `trades.json`.
2. Recalculates win rate, average win, average loss over the last 50 trades.
3. Sizes the next trade using the **half-Kelly criterion**.
4. Optionally adjusts `STOP_LOSS_ATR_MULT`, `TAKE_PROFIT_ATR_MULT`, or `MIN_RR_RATIO` based on performance trends.

---

## Project structure

```
trader-/
├── bot.py                  # Main 24/7 runner
├── coinbase_client.py      # Coinbase Advanced Trade API wrapper
├── config.py               # Environment-variable configuration
├── learning.py             # Trade history & adaptive parameter tuning
├── strategy.py             # BUY / SELL / HOLD decision logic
├── technical_analysis.py   # Pivots, S/R, market structure, RSI, ATR, EMA
├── requirements.txt
├── .env.example
├── .gitignore
└── tests/
    └── test_technical_analysis.py
```

---

## Running tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Disclaimer

This software is provided for **educational purposes only**.  Cryptocurrency trading involves significant financial risk.  Past performance does not guarantee future results.  Always test thoroughly in dry-run mode before deploying real capital.  The authors accept no liability for financial losses.
