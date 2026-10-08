"""VWAP and explainable volume features."""

import pandas as pd

from app.indicators.models import ScalarSeries, VolumeResult, VolumeState
from app.indicators.moving_averages import scalar_result


def rolling_vwap(frame: pd.DataFrame, period: int = 20) -> ScalarSeries:
    """Rolling typical-price VWAP; no exchange session boundary is assumed."""

    numeric = frame.astype(float)
    typical = (numeric["high"] + numeric["low"] + numeric["close"]) / 3
    numerator = (typical * numeric["volume"]).rolling(period, min_periods=period).sum()
    denominator = numeric["volume"].rolling(period, min_periods=period).sum().replace(0, pd.NA)
    return scalar_result(numerator / denominator, period)


def utc_daily_vwap(frame: pd.DataFrame) -> ScalarSeries:
    """Cumulative typical-price VWAP reset at each UTC calendar day."""

    numeric = frame[["high", "low", "close", "volume"]].astype(float)
    typical = (numeric["high"] + numeric["low"] + numeric["close"]) / 3
    days = pd.DatetimeIndex(frame.index).tz_convert("UTC").date
    cumulative_value = (typical * numeric["volume"]).groupby(days).cumsum()
    cumulative_volume = numeric["volume"].groupby(days).cumsum().replace(0, pd.NA)
    return scalar_result(cumulative_value / cumulative_volume, 1)


def volume_analysis(values: pd.Series, baseline: int = 20) -> VolumeResult:
    volumes = values.astype(float)
    average = volumes.rolling(baseline, min_periods=baseline).mean()
    deviation = volumes.rolling(baseline, min_periods=baseline).std(ddof=0)
    relative = volumes / average.replace(0, pd.NA)
    z_score = (volumes - average) / deviation.replace(0, pd.NA)
    sma_result = scalar_result(average, baseline)
    relative_result = scalar_result(relative, baseline)
    z_result = scalar_result(z_score, baseline)
    if relative_result.current is None:
        state = VolumeState.INSUFFICIENT
        reasons = (f"requires {baseline} candles for a volume baseline",)
    elif relative_result.current >= 1.5:
        state = VolumeState.EXPANSION
        reasons = (f"current volume is {relative_result.current:.2f}x its {baseline}-candle mean",)
    elif relative_result.current <= 0.67:
        state = VolumeState.CONTRACTION
        reasons = (f"current volume is {relative_result.current:.2f}x its {baseline}-candle mean",)
    else:
        state = VolumeState.NORMAL
        reasons = (f"current volume is {relative_result.current:.2f}x its {baseline}-candle mean",)
    return VolumeResult(
        raw=float(volumes.iloc[-1]),
        sma=sma_result,
        relative=relative_result,
        z_score=z_result,
        state=state,
        reasons=reasons,
    )
