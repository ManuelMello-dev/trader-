"""Trade history persistence and adaptive learning.

Each completed trade is stored as a JSON record.  The module exposes helpers to:
- Append a new trade record.
- Load the full history.
- Compute performance metrics (win rate, avg win/loss).
- Suggest updated strategy parameters based on recent performance.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any

import config

logger = logging.getLogger(__name__)


# ── Trade record ───────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """An immutable snapshot of a completed trade."""

    product_id: str
    side: str          # "BUY" or "SELL" (the closing side)
    entry_price: float
    exit_price: float
    base_size: float
    entry_time: int    # Unix timestamp (seconds)
    exit_time: int
    stop_loss: float
    take_profit: float
    pnl_pct: float     # (exit - entry) / entry  [for a long]
    reason: str        # Why the trade was closed
    dry_run: bool


# ── Storage ────────────────────────────────────────────────────────────────────

def load_trades(path: str = config.TRADE_LOG_FILE) -> list[dict[str, Any]]:
    """Return all trade records from *path*."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load trade log: %s", exc)
        return []


def save_trade(record: TradeRecord, path: str = config.TRADE_LOG_FILE) -> None:
    """Append *record* to the JSON trade log at *path*."""
    trades = load_trades(path)
    trades.append(asdict(record))
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(trades, fh, indent=2)
        logger.info(
            "Trade saved: %s  PnL=%.2f%%  reason=%s",
            record.product_id,
            record.pnl_pct * 100,
            record.reason,
        )
    except OSError as exc:
        logger.error("Failed to save trade: %s", exc)


# ── Metrics ────────────────────────────────────────────────────────────────────

def compute_metrics(
    trades: list[dict[str, Any]],
    recent_n: int = 50,
) -> dict[str, float]:
    """Compute performance metrics over the last *recent_n* trades.

    Returns
    -------
    dict with keys:
        ``win_rate``    – fraction of winning trades [0, 1]
        ``avg_win``     – mean PnL of winning trades (positive fraction)
        ``avg_loss``    – mean absolute PnL of losing trades (positive fraction)
        ``profit_factor``– ratio of gross profit to gross loss
        ``expectancy``  – expected PnL per trade
        ``total_trades``– number of completed trades considered
    """
    subset = trades[-recent_n:] if len(trades) > recent_n else trades

    if not subset:
        return {
            "win_rate": 0.5,
            "avg_win": 0.01,
            "avg_loss": 0.01,
            "profit_factor": 1.0,
            "expectancy": 0.0,
            "total_trades": 0,
        }

    wins = [t["pnl_pct"] for t in subset if t["pnl_pct"] > 0]
    losses = [abs(t["pnl_pct"]) for t in subset if t["pnl_pct"] <= 0]

    n = len(subset)
    win_rate = len(wins) / n if n > 0 else 0.5
    avg_win = sum(wins) / len(wins) if wins else 0.01
    avg_loss = sum(losses) / len(losses) if losses else 0.01

    gross_profit = sum(wins)
    gross_loss = sum(losses)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0

    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    return {
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "total_trades": n,
    }


# ── Adaptive suggestions ───────────────────────────────────────────────────────

def suggest_parameters(metrics: dict[str, float]) -> dict[str, float]:
    """Return suggested parameter overrides based on recent performance.

    Currently adjusts:
    - ``stop_loss_atr_mult``: widen when losses are large relative to wins.
    - ``take_profit_atr_mult``: widen when win rate is high.
    - ``min_rr_ratio``: tighten when win rate is low.

    The caller is responsible for deciding whether to apply these.
    """
    win_rate = metrics.get("win_rate", 0.5)
    avg_win = metrics.get("avg_win", 0.01)
    avg_loss = metrics.get("avg_loss", 0.01)
    total = metrics.get("total_trades", 0)

    # Don't adapt until we have at least 10 trades
    if total < 10:
        return {}

    suggestions: dict[str, float] = {}

    # If losses are too large, widen stop (give trades more room)
    if avg_loss > avg_win * 0.8:
        suggestions["stop_loss_atr_mult"] = min(
            config.STOP_LOSS_ATR_MULT * 1.1, 3.0
        )

    # If win rate is high, reach for larger profits
    if win_rate > 0.60:
        suggestions["take_profit_atr_mult"] = min(
            config.TAKE_PROFIT_ATR_MULT * 1.1, 6.0
        )

    # If win rate is low, demand better setups
    if win_rate < 0.40:
        suggestions["min_rr_ratio"] = min(config.MIN_RR_RATIO * 1.1, 4.0)

    if suggestions:
        logger.info("Adaptive suggestions: %s", suggestions)

    return suggestions
