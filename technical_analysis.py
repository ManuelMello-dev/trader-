"""Technical analysis utilities.

Provides:
- Swing-high / swing-low pivot detection
- Support and resistance level clustering
- Market structure identification (HH/HL uptrend, LH/LL downtrend)
- Structure-break and structure-rejection detection
- RSI, ATR and EMA indicators
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class Candle:
    """Single OHLCV candle."""

    start: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Pivot:
    """A swing high or swing low."""

    index: int
    price: float
    is_high: bool  # True = swing high, False = swing low


@dataclass
class Level:
    """A support or resistance price level derived from clustered pivots."""

    price: float
    touches: int = 1
    is_resistance: bool = False  # True = resistance, False = support


@dataclass
class MarketStructure:
    """Summary of the current market structure."""

    trend: str = "NEUTRAL"  # "UPTREND" | "DOWNTREND" | "NEUTRAL"
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    prev_swing_high: float | None = None
    prev_swing_low: float | None = None
    structure_broken: bool = False  # True when last candle broke structure
    rejection: bool = False  # True when a rejection candle is present


# ── Pivot detection ────────────────────────────────────────────────────────────

def find_pivots(candles: Sequence[Candle], window: int = 5) -> list[Pivot]:
    """Return swing highs and lows.

    A swing high at index *i* exists when ``candle[i].high`` is the highest
    value within ``[i-window, i+window]``.  Swing lows use ``candle[i].low``.

    Parameters
    ----------
    candles:
        OHLCV candle sequence, oldest-first.
    window:
        Number of bars on each side to check.

    Returns
    -------
    list[Pivot]
        Pivots sorted by index (oldest first).
    """
    pivots: list[Pivot] = []
    n = len(candles)

    for i in range(window, n - window):
        hi = candles[i].high
        lo = candles[i].low

        # Swing high: highest in the window
        if all(hi >= candles[j].high for j in range(i - window, i + window + 1) if j != i):
            pivots.append(Pivot(index=i, price=hi, is_high=True))

        # Swing low: lowest in the window
        if all(lo <= candles[j].low for j in range(i - window, i + window + 1) if j != i):
            pivots.append(Pivot(index=i, price=lo, is_high=False))

    return sorted(pivots, key=lambda p: p.index)


# ── Support / Resistance clustering ───────────────────────────────────────────

def find_levels(
    pivots: list[Pivot],
    current_price: float,
    cluster_pct: float = 0.005,
) -> tuple[list[Level], list[Level]]:
    """Cluster pivot prices into support and resistance levels.

    Pivots within *cluster_pct* of each other (relative distance) are merged
    into a single level.  The number of contributing pivots becomes the
    ``touches`` count, which signals level strength.

    Parameters
    ----------
    pivots:
        Output of :func:`find_pivots`.
    current_price:
        Latest market price; used to classify levels as support / resistance.
    cluster_pct:
        Relative price tolerance for merging nearby pivots (e.g. 0.005 = 0.5 %).

    Returns
    -------
    (supports, resistances):
        Two lists of :class:`Level` objects sorted by price.
    """
    if not pivots:
        return [], []

    prices = [p.price for p in pivots]
    levels: list[Level] = []

    used = [False] * len(prices)
    for i, base_price in enumerate(prices):
        if used[i]:
            continue
        cluster = [base_price]
        for j in range(i + 1, len(prices)):
            if not used[j] and _rel_diff(prices[j], base_price) <= cluster_pct:
                cluster.append(prices[j])
                used[j] = True
        avg = sum(cluster) / len(cluster)
        levels.append(Level(price=avg, touches=len(cluster)))
        used[i] = True

    supports = sorted(
        [lv for lv in levels if lv.price < current_price],
        key=lambda lv: lv.price,
        reverse=True,  # nearest support first
    )
    resistances = sorted(
        [lv for lv in levels if lv.price >= current_price],
        key=lambda lv: lv.price,
    )

    for lv in resistances:
        lv.is_resistance = True

    return supports, resistances


def _rel_diff(a: float, b: float) -> float:
    mid = (a + b) / 2
    if mid == 0:
        return 0.0
    return abs(a - b) / mid


# ── Market structure ───────────────────────────────────────────────────────────

def get_market_structure(pivots: list[Pivot]) -> MarketStructure:
    """Determine trend from the sequence of swing highs and lows.

    Rules:
    - **Uptrend**: Last swing high > previous swing high AND
                   last swing low  > previous swing low  (HH + HL).
    - **Downtrend**: Last swing high < previous swing high AND
                     last swing low  < previous swing low  (LH + LL).
    - Otherwise **Neutral**.

    Returns
    -------
    MarketStructure
    """
    highs = [p for p in pivots if p.is_high]
    lows = [p for p in pivots if not p.is_high]

    ms = MarketStructure()
    if len(highs) >= 2:
        ms.last_swing_high = highs[-1].price
        ms.prev_swing_high = highs[-2].price
    if len(lows) >= 2:
        ms.last_swing_low = lows[-1].price
        ms.prev_swing_low = lows[-2].price

    if (
        ms.last_swing_high is not None
        and ms.prev_swing_high is not None
        and ms.last_swing_low is not None
        and ms.prev_swing_low is not None
    ):
        hh = ms.last_swing_high > ms.prev_swing_high
        hl = ms.last_swing_low > ms.prev_swing_low
        lh = ms.last_swing_high < ms.prev_swing_high
        ll = ms.last_swing_low < ms.prev_swing_low

        if hh and hl:
            ms.trend = "UPTREND"
        elif lh and ll:
            ms.trend = "DOWNTREND"

    return ms


def detect_structure_break(
    ms: MarketStructure,
    candle: Candle,
) -> bool:
    """Return True when *candle* breaks market structure.

    In an uptrend, a close below the last swing low is a bearish structure
    break (CHoCH — Change of Character).  In a downtrend, a close above the
    last swing high is a bullish structure break.
    """
    if ms.trend == "UPTREND" and ms.last_swing_low is not None:
        return candle.close < ms.last_swing_low
    if ms.trend == "DOWNTREND" and ms.last_swing_high is not None:
        return candle.close > ms.last_swing_high
    return False


def detect_rejection_candle(candle: Candle, is_at_resistance: bool) -> bool:
    """Return True when *candle* shows a strong rejection (wick) pattern.

    At **resistance** we look for a bearish rejection: long upper wick, small
    body, close near the open (shooting star / pin bar).

    At **support** we look for a bullish rejection: long lower wick, small
    body, close near the open (hammer / pin bar).

    The rule of thumb is that the rejection wick must be at least twice the
    body length.
    """
    body = abs(candle.close - candle.open)
    candle_range = candle.high - candle.low
    if candle_range == 0:
        return False

    upper_wick = candle.high - max(candle.close, candle.open)
    lower_wick = min(candle.close, candle.open) - candle.low

    min_wick_ratio = 0.5  # wick must be at least 50% of total range

    if is_at_resistance:
        # Bearish rejection: long upper wick, small body
        upper_dominant = upper_wick >= lower_wick * 1.5
        wick_significant = upper_wick / candle_range >= min_wick_ratio
        return upper_dominant and wick_significant

    else:
        # Bullish rejection (hammer): long lower wick, small body
        lower_dominant = lower_wick >= upper_wick * 1.5
        wick_significant = lower_wick / candle_range >= min_wick_ratio
        return lower_dominant and wick_significant


# ── Proximity helpers ──────────────────────────────────────────────────────────

def is_near_level(price: float, level: Level, tolerance_pct: float) -> bool:
    """Return True when *price* is within *tolerance_pct* of *level.price*."""
    return _rel_diff(price, level.price) <= tolerance_pct


def nearest_support(supports: list[Level], price: float) -> Level | None:
    """Return the closest support level below *price*, or None."""
    candidates = [lv for lv in supports if lv.price < price]
    if not candidates:
        return None
    return min(candidates, key=lambda lv: abs(lv.price - price))


def nearest_resistance(resistances: list[Level], price: float) -> Level | None:
    """Return the closest resistance level above *price*, or None."""
    candidates = [lv for lv in resistances if lv.price > price]
    if not candidates:
        return None
    return min(candidates, key=lambda lv: abs(lv.price - price))


# ── Indicators ─────────────────────────────────────────────────────────────────

def calculate_rsi(closes: Sequence[float], period: int = 14) -> float:
    """Compute the RSI of the last bar in *closes*.

    Uses Wilder's smoothed moving average (standard RSI formula).

    Parameters
    ----------
    closes:
        Sequence of closing prices (oldest first), length > period.
    period:
        Look-back period (default 14).

    Returns
    -------
    float
        RSI value in [0, 100], or 50.0 when there is insufficient data.
    """
    if len(closes) < period + 1:
        return 50.0

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_atr(
    candles: Sequence[Candle], period: int = 14
) -> float:
    """Compute the Average True Range of the most recent bar.

    Returns 0.0 when there is insufficient data.
    """
    if len(candles) < period + 1:
        return 0.0

    trs: list[float] = []
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    # Wilder's smoothed average
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr


def calculate_ema(values: Sequence[float], period: int) -> float:
    """Return the EMA of the last element in *values*.

    Returns the simple mean when there is insufficient data.
    """
    if len(values) == 0:
        return 0.0
    vals = list(values)
    if len(vals) < period:
        return sum(vals) / len(vals)

    k = 2.0 / (period + 1)
    ema = sum(vals[:period]) / period
    for v in vals[period:]:
        ema = v * k + ema * (1 - k)
    return ema
