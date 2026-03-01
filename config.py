"""Configuration loaded from environment variables / .env file."""

import os
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(key, default)
    if required and not value:
        raise EnvironmentError(f"Required environment variable '{key}' is not set.")
    return value


# ── Coinbase credentials ───────────────────────────────────────────────────────
API_KEY: str | None = _get("COINBASE_API_KEY")
API_SECRET: str | None = _get("COINBASE_API_SECRET")

# ── Trading parameters ─────────────────────────────────────────────────────────
TRADING_PAIR: str = _get("TRADING_PAIR", "BTC-USD")
QUOTE_CURRENCY: str = _get("QUOTE_CURRENCY", "USD")
MAX_POSITION_PCT: float = float(_get("MAX_POSITION_PCT", "0.10"))
STOP_LOSS_ATR_MULT: float = float(_get("STOP_LOSS_ATR_MULT", "1.5"))
TAKE_PROFIT_ATR_MULT: float = float(_get("TAKE_PROFIT_ATR_MULT", "3.0"))
MIN_RR_RATIO: float = float(_get("MIN_RR_RATIO", "2.0"))
CANDLE_GRANULARITY: str = _get("CANDLE_GRANULARITY", "ONE_HOUR")
LOOKBACK_CANDLES: int = int(_get("LOOKBACK_CANDLES", "200"))
PIVOT_WINDOW: int = int(_get("PIVOT_WINDOW", "5"))
SR_CLUSTER_PCT: float = float(_get("SR_CLUSTER_PCT", "0.005"))
BOT_INTERVAL_SECONDS: int = int(_get("BOT_INTERVAL_SECONDS", "300"))
DRY_RUN: bool = _get("DRY_RUN", "true").lower() in ("true", "1", "yes")
DRY_RUN_BALANCE: float = float(_get("DRY_RUN_BALANCE", "10000.0"))
LOG_LEVEL: str = _get("LOG_LEVEL", "INFO").upper()
TRADE_LOG_FILE: str = _get("TRADE_LOG_FILE", "trades.json")

# ── Pair auto-selection ────────────────────────────────────────────────────────
AUTO_SELECT_PAIR: bool = _get("AUTO_SELECT_PAIR", "false").lower() in ("true", "1", "yes")
CANDIDATE_PAIRS: list[str] = [
    p.strip()
    for p in _get("CANDIDATE_PAIRS", "BTC-USD,ETH-USD,SOL-USD").split(",")
    if p.strip()
]
