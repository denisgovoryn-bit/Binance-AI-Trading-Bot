"""RSI, MACD, and normalized price momentum."""

import numpy as np
import pandas as pd

from app.indicators.models import MACDResult, MomentumResult, ScalarSeries
from app.indicators.moving_averages import scalar_result


def rsi(values: pd.Series, period: int = 14) -> ScalarSeries:
    """Wilder RSI using exponentially smoothed gains and losses."""

    delta = values.astype(float).diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    result = pd.Series(np.nan, index=values.index, dtype=float)
    if len(values) > period:
        average_gain = float(gain.iloc[1 : period + 1].mean())
        average_loss = float(loss.iloc[1 : period + 1].mean())
        result.iloc[period] = _rsi_value(average_gain, average_loss)
        for index in range(period + 1, len(values)):
            average_gain = (average_gain * (period - 1) + float(gain.iloc[index])) / period
            average_loss = (average_loss * (period - 1) + float(loss.iloc[index])) / period
            result.iloc[index] = _rsi_value(average_gain, average_loss)
    return scalar_result(result, period + 1)


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100 - 100 / (1 + average_gain / average_loss)


def macd(
    values: pd.Series, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9
) -> MACDResult:
    if fast_period >= slow_period:
        raise ValueError("MACD fast period must be less than slow period")
    prices = values.astype(float)
    fast = prices.ewm(span=fast_period, adjust=False, min_periods=fast_period).mean()
    slow = prices.ewm(span=slow_period, adjust=False, min_periods=slow_period).mean()
    line = fast - slow
    signal = line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
    histogram = line - signal
    required = slow_period + signal_period - 1
    histogram_values = histogram.dropna()
    change = (
        float(histogram_values.iloc[-1] - histogram_values.iloc[-2])
        if len(histogram_values) >= 2
        else None
    )
    direction = (
        None
        if change is None
        else ("RISING" if change > 0 else "FALLING" if change < 0 else "FLAT")
    )
    return MACDResult(
        macd=scalar_result(line, slow_period),
        signal=scalar_result(signal, required),
        histogram=scalar_result(histogram, required),
        histogram_change=change,
        histogram_direction=direction,
    )


def price_momentum(values: pd.Series, roc_period: int = 10) -> MomentumResult:
    prices = values.astype(float)
    returns = {
        period: (
            float(prices.iloc[-1] / prices.iloc[-1 - period] - 1) if len(prices) > period else None
        )
        for period in (1, 3, 5, 10)
    }
    roc = prices.pct_change(roc_period) * 100
    one_period = prices.pct_change()
    acceleration = float(one_period.iloc[-1] - one_period.iloc[-2]) if len(prices) >= 3 else None
    return MomentumResult(
        returns=returns,
        rate_of_change=scalar_result(roc, roc_period + 1),
        acceleration=acceleration,
    )
