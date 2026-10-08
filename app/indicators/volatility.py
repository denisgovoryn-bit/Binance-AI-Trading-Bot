"""ATR, ADX, Bollinger Bands, and relative volatility regimes."""

import numpy as np
import pandas as pd

from app.indicators.models import (
    ADXResult,
    ATRResult,
    BollingerResult,
    VolatilityRegime,
    VolatilityResult,
)
from app.indicators.moving_averages import scalar_result


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> ATRResult:
    tr = true_range(frame.astype(float))
    absolute = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    percentage = absolute / frame["close"].astype(float) * 100
    return ATRResult(
        absolute=scalar_result(absolute, period),
        percentage=scalar_result(percentage, period),
    )


def adx(frame: pd.DataFrame, period: int = 14) -> ADXResult:
    numeric = frame.astype(float)
    upward = numeric["high"].diff()
    downward = -numeric["low"].diff()
    plus_dm = pd.Series(
        np.where((upward > downward) & (upward > 0), upward, 0.0), index=frame.index
    )
    minus_dm = pd.Series(
        np.where((downward > upward) & (downward > 0), downward, 0.0), index=frame.index
    )
    tr_smoothed = true_range(numeric).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = (
        100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr_smoothed
    )
    minus_di = (
        100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr_smoothed
    )
    denominator = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denominator
    adx_values = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return ADXResult(
        adx=scalar_result(adx_values, period * 2 - 1),
        plus_di=scalar_result(plus_di, period),
        minus_di=scalar_result(minus_di, period),
    )


def bollinger_bands(
    values: pd.Series, period: int = 20, standard_deviations: float = 2
) -> BollingerResult:
    prices = values.astype(float)
    middle = prices.rolling(period, min_periods=period).mean()
    deviation = prices.rolling(period, min_periods=period).std(ddof=0)
    upper = middle + standard_deviations * deviation
    lower = middle - standard_deviations * deviation
    width = (upper - lower) / middle * 100
    span = (upper - lower).replace(0, np.nan)
    position = (prices - lower) / span
    return BollingerResult(
        middle=scalar_result(middle, period),
        upper=scalar_result(upper, period),
        lower=scalar_result(lower, period),
        width_percentage=scalar_result(width, period),
        price_position=scalar_result(position, period),
    )


def volatility_regime(
    atr_percentages: tuple[float | None, ...], lookback: int = 100
) -> VolatilityResult:
    available = [value for value in atr_percentages if value is not None]
    if len(available) < lookback:
        return VolatilityResult(
            regime=VolatilityRegime.INSUFFICIENT,
            atr_percentage=available[-1] if available else None,
            percentile=None,
            reasons=(f"requires {lookback} ATR observations",),
        )
    sample = available[-lookback:]
    current = sample[-1]
    percentile = sum(value <= current for value in sample) / len(sample) * 100
    if percentile >= 95:
        regime = VolatilityRegime.EXTREME
    elif percentile >= 75:
        regime = VolatilityRegime.HIGH
    elif percentile <= 25:
        regime = VolatilityRegime.LOW
    else:
        regime = VolatilityRegime.NORMAL
    return VolatilityResult(
        regime=regime,
        atr_percentage=current,
        percentile=percentile,
        reasons=(
            f"ATR% is at the {percentile:.1f} percentile of the last {lookback} observations",
        ),
    )
