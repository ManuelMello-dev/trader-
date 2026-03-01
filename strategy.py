"""Trading strategy: decide whether to BUY, SELL, or HOLD.

Decision flow
─────────────
1. Compute pivots, S/R levels, market structure, RSI, ATR from recent candles.
2. **No open position (FLAT)**
   - Require uptrend (HH + HL) OR a bullish structure break.
   - Price must be near a support level.
   - A bullish rejection candle on the latest bar strengthens the signal.
   - RSI must be below the overbought threshold (default 65).
   - Reward : risk ratio must meet the minimum configured.
   - ✅ → BUY, with stop-loss below support and take-profit at nearest resistance.
3. **Open position (LONG)**
   a. Structure continues (uptrend intact, no break) → HOLD.
   b. Price has reached the take-profit target             → SELL (profit).
   c. Price has hit or crossed the stop-loss               → SELL (stop).
   d. Bearish structure break (CHoCH)                      → SELL (structure).
   e. Bearish rejection candle at resistance               → SELL (rejection).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

import config
import technical_analysis as ta
from technical_analysis import Candle, Level, MarketStructure

logger = logging.getLogger(__name__)


# ── Position state ─────────────────────────────────────────────────────────────

@dataclass
class Position:
    """Tracks an open long position."""

    entry_price: float = 0.0
    base_size: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    is_open: bool = False


# ── Decision ───────────────────────────────────────────────────────────────────

@dataclass
class Decision:
    """Output of :func:`evaluate`."""

    action: str = "HOLD"  # "BUY" | "SELL" | "HOLD"
    reason: str = ""
    stop_loss: float = 0.0
    take_profit: float = 0.0
    # Size expressed as a fraction of available capital (0–1)
    position_size_pct: float = config.MAX_POSITION_PCT
    supports: list[Level] = field(default_factory=list)
    resistances: list[Level] = field(default_factory=list)
    rsi: float = 50.0
    atr: float = 0.0
    trend: str = "NEUTRAL"


# ── Main entry point ───────────────────────────────────────────────────────────

def evaluate(
    candles: Sequence[Candle],
    position: Position,
    win_rate: float = 0.5,
    avg_win: float = 1.0,
    avg_loss: float = 1.0,
) -> Decision:
    """Analyse *candles* and return a :class:`Decision`.

    Parameters
    ----------
    candles:
        Recent OHLCV bars, oldest-first.  Minimum length:
        ``config.PIVOT_WINDOW * 2 + 15`` (pivot window + indicator periods).
    position:
        Current open position (or a closed/empty one when flat).
    win_rate:
        Historical win rate from :mod:`learning` (used for Kelly sizing).
    avg_win / avg_loss:
        Average win/loss ratio from :mod:`learning`.
    """
    decision = Decision()

    if len(candles) < config.PIVOT_WINDOW * 2 + 15:
        decision.action = "HOLD"
        decision.reason = "Insufficient candles for analysis"
        return decision

    current = candles[-1]
    price = current.close

    # ── Technical indicators ───────────────────────────────────────────────────
    closes = [c.close for c in candles]
    rsi = ta.calculate_rsi(closes)
    atr = ta.calculate_atr(list(candles))
    pivots = ta.find_pivots(list(candles), window=config.PIVOT_WINDOW)
    supports, resistances = ta.find_levels(pivots, price, config.SR_CLUSTER_PCT)
    ms = ta.get_market_structure(pivots)

    decision.supports = supports
    decision.resistances = resistances
    decision.rsi = rsi
    decision.atr = atr
    decision.trend = ms.trend

    nearest_sup = ta.nearest_support(supports, price)
    nearest_res = ta.nearest_resistance(resistances, price)

    # ── Flat (no position) → look for entry ───────────────────────────────────
    if not position.is_open:
        return _evaluate_entry(
            decision=decision,
            candles=candles,
            current=current,
            price=price,
            rsi=rsi,
            atr=atr,
            ms=ms,
            nearest_sup=nearest_sup,
            nearest_res=nearest_res,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
        )

    # ── Open position → manage exit ───────────────────────────────────────────
    return _evaluate_exit(
        decision=decision,
        current=current,
        price=price,
        ms=ms,
        position=position,
        nearest_res=nearest_res,
    )


# ── Entry logic ────────────────────────────────────────────────────────────────

def _evaluate_entry(
    *,
    decision: Decision,
    candles: Sequence[Candle],
    current: Candle,
    price: float,
    rsi: float,
    atr: float,
    ms: MarketStructure,
    nearest_sup: Level | None,
    nearest_res: Level | None,
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> Decision:
    decision.action = "HOLD"

    # ── Gate 1: trend must be bullish ──────────────────────────────────────────
    if ms.trend not in ("UPTREND", "NEUTRAL"):
        decision.reason = f"No long entry in {ms.trend}"
        return decision

    # ── Gate 2: must be near a support level ──────────────────────────────────
    if nearest_sup is None:
        decision.reason = "No support level identified"
        return decision

    # Use 3× SR_CLUSTER_PCT so entries are allowed slightly away from the
    # exact level (same widening is applied to resistance proximity checks).
    at_support = ta.is_near_level(price, nearest_sup, tolerance_pct=config.SR_CLUSTER_PCT * 3)
    if not at_support:
        decision.reason = (
            f"Price {price:.4f} not near support {nearest_sup.price:.4f}"
        )
        return decision

    # ── Gate 3: RSI not overbought ─────────────────────────────────────────────
    rsi_threshold = 65.0
    if rsi > rsi_threshold:
        decision.reason = f"RSI {rsi:.1f} overbought (>{rsi_threshold})"
        return decision

    # ── Gate 4: reward/risk ratio check ───────────────────────────────────────
    if atr <= 0:
        decision.reason = "ATR is zero, cannot size stop-loss"
        return decision

    stop_loss = nearest_sup.price - atr * config.STOP_LOSS_ATR_MULT
    if nearest_res is not None:
        take_profit = nearest_res.price
    else:
        take_profit = price + atr * config.TAKE_PROFIT_ATR_MULT

    risk = price - stop_loss
    reward = take_profit - price

    if risk <= 0:
        decision.reason = "Stop-loss is above entry price"
        return decision

    rr = reward / risk
    if rr < config.MIN_RR_RATIO:
        decision.reason = (
            f"RR {rr:.2f} below minimum {config.MIN_RR_RATIO}"
        )
        return decision

    # ── Gate 5: confirmation (rejection candle or strong support) ─────────────
    rejection = ta.detect_rejection_candle(current, is_at_resistance=False)
    strong_support = nearest_sup.touches >= 2
    if not rejection and not strong_support and ms.trend != "UPTREND":
        decision.reason = "No confirmation (no rejection candle or strong support)"
        return decision

    # ── Position size via fractional Kelly ────────────────────────────────────
    size_pct = _kelly_fraction(win_rate, avg_win, avg_loss, max_pct=config.MAX_POSITION_PCT)

    decision.action = "BUY"
    decision.reason = (
        f"Support={nearest_sup.price:.4f} (touches={nearest_sup.touches}) "
        f"Trend={ms.trend} RSI={rsi:.1f} RR={rr:.2f} "
        f"Rejection={rejection}"
    )
    decision.stop_loss = stop_loss
    decision.take_profit = take_profit
    decision.position_size_pct = size_pct
    logger.info("BUY signal: %s", decision.reason)
    return decision


# ── Exit logic ─────────────────────────────────────────────────────────────────

def _evaluate_exit(
    *,
    decision: Decision,
    current: Candle,
    price: float,
    ms: MarketStructure,
    position: Position,
    nearest_res: Level | None,
) -> Decision:
    decision.action = "HOLD"

    # ── Priority 1: stop-loss hit ──────────────────────────────────────────────
    if price <= position.stop_loss:
        decision.action = "SELL"
        decision.reason = (
            f"Stop-loss hit: price {price:.4f} <= SL {position.stop_loss:.4f}"
        )
        logger.info("SELL (stop-loss): %s", decision.reason)
        return decision

    # ── Priority 2: take-profit reached ───────────────────────────────────────
    if price >= position.take_profit:
        decision.action = "SELL"
        decision.reason = (
            f"Take-profit reached: price {price:.4f} >= TP {position.take_profit:.4f}"
        )
        logger.info("SELL (take-profit): %s", decision.reason)
        return decision

    # ── Priority 3: bearish structure break ───────────────────────────────────
    if ta.detect_structure_break(ms, current):
        decision.action = "SELL"
        decision.reason = (
            f"Structure break: close {current.close:.4f} "
            f"below last swing low {ms.last_swing_low}"
        )
        logger.info("SELL (structure break): %s", decision.reason)
        return decision

    # ── Priority 4: rejection candle at resistance ─────────────────────────────
    if nearest_res is not None:
        # Use 3× SR_CLUSTER_PCT for the same widened proximity as entry checks.
        at_res = ta.is_near_level(price, nearest_res, tolerance_pct=config.SR_CLUSTER_PCT * 3)
        if at_res and ta.detect_rejection_candle(current, is_at_resistance=True):
            decision.action = "SELL"
            decision.reason = (
                f"Rejection at resistance {nearest_res.price:.4f}: "
                f"bearish pin bar detected"
            )
            logger.info("SELL (rejection): %s", decision.reason)
            return decision

    # ── Hold: structure intact ─────────────────────────────────────────────────
    decision.reason = (
        f"Holding: trend={ms.trend} price={price:.4f} "
        f"SL={position.stop_loss:.4f} TP={position.take_profit:.4f}"
    )
    return decision


# ── Position sizing ────────────────────────────────────────────────────────────

def _kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    max_pct: float = 0.10,
) -> float:
    """Return a conservative (half-Kelly) position size fraction.

    Falls back to *max_pct* when data is insufficient.
    """
    if avg_loss <= 0 or win_rate <= 0:
        return max_pct

    b = avg_win / avg_loss
    kelly = win_rate - (1 - win_rate) / b
    half_kelly = kelly / 2.0

    # Cap at configured maximum
    return max(0.01, min(half_kelly, max_pct))
