"""Unit tests for technical_analysis module."""

from __future__ import annotations

import pytest
from technical_analysis import (
    Candle,
    Pivot,
    Level,
    calculate_atr,
    calculate_ema,
    calculate_rsi,
    detect_rejection_candle,
    detect_structure_break,
    find_levels,
    find_pivots,
    get_market_structure,
    is_near_level,
    nearest_resistance,
    nearest_support,
)


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

def _candle(
    close: float,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
) -> Candle:
    """Create a minimal Candle with sensible defaults."""
    return Candle(
        start=0,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close * 1.005,
        low=low if low is not None else close * 0.995,
        close=close,
        volume=1.0,
    )


def _candles_from_closes(closes: list[float]) -> list[Candle]:
    return [_candle(c) for c in closes]


# ── RSI ────────────────────────────────────────────────────────────────────────

class TestRSI:
    def test_insufficient_data_returns_50(self):
        closes = [100.0] * 5
        assert calculate_rsi(closes, period=14) == 50.0

    def test_all_gains_returns_100(self):
        closes = list(range(1, 30))
        rsi = calculate_rsi([float(c) for c in closes], period=14)
        assert rsi == 100.0

    def test_all_losses_returns_0(self):
        closes = list(range(30, 1, -1))
        rsi = calculate_rsi([float(c) for c in closes], period=14)
        assert rsi == 0.0

    def test_neutral_market_near_50(self):
        # Alternating up/down prices → RSI should be near 50
        closes = [100.0 + (i % 2) * 1.0 for i in range(40)]
        rsi = calculate_rsi(closes, period=14)
        assert 40.0 < rsi < 60.0

    def test_range_is_0_to_100(self):
        import random
        random.seed(42)
        closes = [100.0 + random.uniform(-5, 5) for _ in range(50)]
        rsi = calculate_rsi(closes, period=14)
        assert 0.0 <= rsi <= 100.0


# ── ATR ────────────────────────────────────────────────────────────────────────

class TestATR:
    def test_insufficient_data_returns_zero(self):
        candles = [_candle(100.0)] * 5
        assert calculate_atr(candles, period=14) == 0.0

    def test_constant_candles_atr_equals_range(self):
        # high = close * 1.005, low = close * 0.995 → range ≈ 1 % of 100 = 1.0
        candles = [_candle(100.0)] * 30
        atr = calculate_atr(candles, period=14)
        expected_range = 100.0 * 0.01  # 1.0
        assert abs(atr - expected_range) < 0.01

    def test_atr_positive(self):
        closes = [100.0 + i * 0.5 for i in range(30)]
        candles = _candles_from_closes(closes)
        atr = calculate_atr(candles, period=14)
        assert atr > 0.0


# ── EMA ────────────────────────────────────────────────────────────────────────

class TestEMA:
    def test_single_value(self):
        assert calculate_ema([42.0], period=14) == 42.0

    def test_constant_series(self):
        values = [5.0] * 20
        assert abs(calculate_ema(values, period=14) - 5.0) < 1e-9

    def test_increasing_series_ema_below_last(self):
        values = [float(i) for i in range(1, 21)]
        ema = calculate_ema(values, period=10)
        # EMA should be between the mean and the last value
        assert values[-1] > ema > sum(values) / len(values) * 0.9


# ── find_pivots ────────────────────────────────────────────────────────────────

class TestFindPivots:
    def _make_wave_candles(self) -> list[Candle]:
        """A simple zigzag price series with known highs/lows."""
        prices = [
            100, 102, 105, 103, 101,  # first high near 105
            99,  97,  95,  96,  98,   # first low near 95
            100, 103, 107, 106, 104,  # second high near 107
            102, 100, 96,  94,  95,   # second low near 94
            97,  99, 101,             # tail
        ]
        candles = []
        for i, p in enumerate(prices):
            candles.append(
                Candle(
                    start=i,
                    open=float(p),
                    high=float(p) + 0.5,
                    low=float(p) - 0.5,
                    close=float(p),
                    volume=1.0,
                )
            )
        return candles

    def test_finds_swing_highs_and_lows(self):
        candles = self._make_wave_candles()
        pivots = find_pivots(candles, window=2)
        highs = [p for p in pivots if p.is_high]
        lows = [p for p in pivots if not p.is_high]
        assert len(highs) >= 1
        assert len(lows) >= 1

    def test_pivots_ordered_by_index(self):
        candles = self._make_wave_candles()
        pivots = find_pivots(candles, window=2)
        indices = [p.index for p in pivots]
        assert indices == sorted(indices)

    def test_empty_candles_returns_empty(self):
        assert find_pivots([], window=2) == []

    def test_insufficient_candles_returns_empty(self):
        candles = [_candle(100.0)] * 3
        assert find_pivots(candles, window=5) == []


# ── find_levels ────────────────────────────────────────────────────────────────

class TestFindLevels:
    def test_levels_above_price_are_resistance(self):
        pivots = [
            Pivot(index=0, price=110.0, is_high=True),
            Pivot(index=1, price=90.0, is_high=False),
        ]
        supports, resistances = find_levels(pivots, current_price=100.0)
        assert all(lv.is_resistance for lv in resistances)
        assert not any(lv.is_resistance for lv in supports)

    def test_clustering_merges_close_prices(self):
        pivots = [
            Pivot(index=0, price=100.0, is_high=False),
            Pivot(index=1, price=100.3, is_high=False),  # within 0.5 %
            Pivot(index=2, price=80.0, is_high=False),   # separate cluster
        ]
        supports, _ = find_levels(pivots, current_price=110.0, cluster_pct=0.005)
        prices = [lv.price for lv in supports]
        # 100.0 and 100.3 should merge; 80.0 stays separate
        assert len(prices) == 2
        assert any(lv.touches == 2 for lv in supports)

    def test_empty_pivots_returns_empty_lists(self):
        supports, resistances = find_levels([], current_price=100.0)
        assert supports == []
        assert resistances == []


# ── get_market_structure ───────────────────────────────────────────────────────

class TestMarketStructure:
    def _uptrend_pivots(self) -> list[Pivot]:
        return [
            Pivot(index=0, price=95.0, is_high=False),
            Pivot(index=1, price=105.0, is_high=True),
            Pivot(index=2, price=97.0, is_high=False),   # HL
            Pivot(index=3, price=110.0, is_high=True),   # HH
        ]

    def _downtrend_pivots(self) -> list[Pivot]:
        return [
            Pivot(index=0, price=110.0, is_high=True),
            Pivot(index=1, price=100.0, is_high=False),
            Pivot(index=2, price=107.0, is_high=True),   # LH
            Pivot(index=3, price=96.0, is_high=False),   # LL
        ]

    def test_uptrend_detected(self):
        ms = get_market_structure(self._uptrend_pivots())
        assert ms.trend == "UPTREND"

    def test_downtrend_detected(self):
        ms = get_market_structure(self._downtrend_pivots())
        assert ms.trend == "DOWNTREND"

    def test_neutral_with_insufficient_pivots(self):
        ms = get_market_structure([Pivot(0, 100.0, True)])
        assert ms.trend == "NEUTRAL"

    def test_neutral_mixed_structure(self):
        # HH but LL → mixed, should be NEUTRAL
        pivots = [
            Pivot(0, 90.0, False),
            Pivot(1, 100.0, True),
            Pivot(2, 85.0, False),   # LL (not HL)
            Pivot(3, 105.0, True),   # HH
        ]
        ms = get_market_structure(pivots)
        assert ms.trend == "NEUTRAL"


# ── detect_structure_break ────────────────────────────────────────────────────

class TestStructureBreak:
    def test_uptrend_break_on_close_below_last_low(self):
        from technical_analysis import MarketStructure
        ms = MarketStructure(trend="UPTREND", last_swing_low=100.0)
        candle = _candle(close=98.0)
        assert detect_structure_break(ms, candle) is True

    def test_uptrend_no_break_above_low(self):
        from technical_analysis import MarketStructure
        ms = MarketStructure(trend="UPTREND", last_swing_low=100.0)
        candle = _candle(close=102.0)
        assert detect_structure_break(ms, candle) is False

    def test_downtrend_break_on_close_above_last_high(self):
        from technical_analysis import MarketStructure
        ms = MarketStructure(trend="DOWNTREND", last_swing_high=105.0)
        candle = _candle(close=107.0)
        assert detect_structure_break(ms, candle) is True

    def test_neutral_never_breaks(self):
        from technical_analysis import MarketStructure
        ms = MarketStructure(trend="NEUTRAL")
        candle = _candle(close=50.0)
        assert detect_structure_break(ms, candle) is False


# ── detect_rejection_candle ───────────────────────────────────────────────────

class TestRejectionCandle:
    def _hammer(self) -> Candle:
        """Classic hammer: open near top, long lower wick."""
        return Candle(
            start=0, open=102.0, high=102.5, low=97.0, close=101.5, volume=1.0
        )

    def _shooting_star(self) -> Candle:
        """Classic shooting star: open near bottom, long upper wick."""
        return Candle(
            start=0, open=98.0, high=104.0, low=97.5, close=98.5, volume=1.0
        )

    def _doji(self) -> Candle:
        """Doji: roughly equal wicks – not a clear rejection."""
        return Candle(
            start=0, open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0
        )

    def test_hammer_at_support(self):
        assert detect_rejection_candle(self._hammer(), is_at_resistance=False) is True

    def test_shooting_star_at_resistance(self):
        assert detect_rejection_candle(self._shooting_star(), is_at_resistance=True) is True

    def test_hammer_not_rejection_at_resistance(self):
        assert detect_rejection_candle(self._hammer(), is_at_resistance=True) is False

    def test_doji_not_rejection(self):
        # Doji has roughly equal wicks – should fail the dominance test
        assert detect_rejection_candle(self._doji(), is_at_resistance=False) is False

    def test_zero_range_candle(self):
        candle = Candle(start=0, open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0)
        assert detect_rejection_candle(candle, is_at_resistance=True) is False


# ── is_near_level / nearest helpers ───────────────────────────────────────────

class TestProximityHelpers:
    def test_is_near_level_within_tolerance(self):
        lv = Level(price=100.0)
        assert is_near_level(100.3, lv, 0.005) is True  # 0.3 % < 0.5 %

    def test_is_near_level_outside_tolerance(self):
        lv = Level(price=100.0)
        assert is_near_level(101.0, lv, 0.005) is False  # 1 % > 0.5 %

    def test_nearest_support_returns_closest_below(self):
        supports = [Level(price=90.0), Level(price=95.0), Level(price=98.0)]
        result = nearest_support(supports, price=100.0)
        assert result is not None
        assert result.price == 98.0

    def test_nearest_support_none_when_empty(self):
        assert nearest_support([], price=100.0) is None

    def test_nearest_resistance_returns_closest_above(self):
        resistances = [Level(price=105.0, is_resistance=True),
                       Level(price=110.0, is_resistance=True)]
        result = nearest_resistance(resistances, price=100.0)
        assert result is not None
        assert result.price == 105.0

    def test_nearest_resistance_none_when_empty(self):
        assert nearest_resistance([], price=100.0) is None
