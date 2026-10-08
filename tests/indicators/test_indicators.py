import math

import pandas as pd
import pytest

from app.indicators.models import VolatilityRegime, VolumeState
from app.indicators.momentum import macd, price_momentum, rsi
from app.indicators.moving_averages import ema
from app.indicators.volatility import adx, atr, bollinger_bands, volatility_regime
from app.indicators.volume import rolling_vwap, volume_analysis


def test_ema_matches_recursive_reference_values() -> None:
    result = ema(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), period=3)
    assert result.series[:2] == (None, None)
    assert result.series[2:] == pytest.approx((2.25, 3.125, 4.0625))
    assert result.current == pytest.approx(4.0625)


def test_ema_explicitly_reports_insufficient_history() -> None:
    result = ema(pd.Series([1.0, 2.0]), period=3)
    assert result.current is None
    assert not result.sufficient
    assert result.required_history == 3


def test_wilder_rsi_matches_known_reference_sequence() -> None:
    closes = pd.Series(
        [
            44.34,
            44.09,
            44.15,
            43.61,
            44.33,
            44.83,
            45.10,
            45.42,
            45.84,
            46.08,
            45.89,
            46.03,
            45.61,
            46.28,
            46.28,
        ]
    )
    result = rsi(closes, 14)
    assert result.current == pytest.approx(70.46, abs=0.1)
    assert result.sufficient


def test_macd_zero_for_constant_prices_and_reports_flat_histogram() -> None:
    result = macd(pd.Series([100.0] * 40))
    assert result.macd.current == pytest.approx(0)
    assert result.signal.current == pytest.approx(0)
    assert result.histogram.current == pytest.approx(0)
    assert result.histogram_direction == "FLAT"


def test_atr_matches_constant_true_range_and_percentage() -> None:
    frame = pd.DataFrame({"high": [11.0] * 20, "low": [9.0] * 20, "close": [10.0] * 20})
    result = atr(frame, 14)
    assert result.absolute.current == pytest.approx(2.0)
    assert result.percentage.current == pytest.approx(20.0)


def test_adx_reports_directional_strength_without_directional_sign() -> None:
    frame = pd.DataFrame({"high": range(11, 51), "low": range(9, 49), "close": range(10, 50)})
    result = adx(frame, 14)
    assert result.adx.current == pytest.approx(100.0)
    assert result.plus_di.current is not None and result.plus_di.current > 0
    assert result.minus_di.current == pytest.approx(0.0)


def test_bollinger_reference_and_price_position() -> None:
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = bollinger_bands(values, period=5, standard_deviations=2)
    assert result.middle.current == pytest.approx(3.0)
    assert result.upper.current == pytest.approx(3 + 2 * math.sqrt(2))
    assert result.lower.current == pytest.approx(3 - 2 * math.sqrt(2))
    assert result.price_position.current == pytest.approx(
        (5 - (3 - 2 * math.sqrt(2))) / (4 * math.sqrt(2))
    )


def test_rolling_vwap_uses_typical_price_weighted_by_volume() -> None:
    frame = pd.DataFrame(
        {"high": [11.0, 13.0], "low": [9.0, 11.0], "close": [10.0, 12.0], "volume": [1.0, 3.0]}
    )
    result = rolling_vwap(frame, 2)
    assert result.current == pytest.approx(11.5)


def test_relative_volume_requires_baseline_then_detects_expansion() -> None:
    insufficient = volume_analysis(pd.Series([1.0] * 19), 20)
    expanded = volume_analysis(pd.Series([1.0] * 19 + [10.0]), 20)
    assert insufficient.state is VolumeState.INSUFFICIENT
    assert expanded.relative.current == pytest.approx(10 / 1.45)
    assert expanded.state is VolumeState.EXPANSION


def test_momentum_returns_and_acceleration_are_normalized() -> None:
    result = price_momentum(pd.Series([100.0, 101.0, 103.0, 106.0]))
    assert result.returns[1] == pytest.approx(106 / 103 - 1)
    assert result.returns[3] == pytest.approx(0.06)
    assert result.acceleration == pytest.approx((106 / 103 - 1) - (103 / 101 - 1))


def test_volatility_regime_uses_rolling_percentile() -> None:
    result = volatility_regime(tuple(float(index) for index in range(1, 101)), 100)
    assert result.regime is VolatilityRegime.EXTREME
    assert result.percentile == 100
