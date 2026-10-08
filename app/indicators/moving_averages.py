"""Moving-average calculations."""

import pandas as pd

from app.indicators.models import ScalarSeries


def clean_series(series: pd.Series) -> tuple[float | None, ...]:
    return tuple(None if pd.isna(value) else float(value) for value in series)


def scalar_result(series: pd.Series, required_history: int) -> ScalarSeries:
    values = clean_series(series)
    current = values[-1] if values else None
    return ScalarSeries(
        current=current,
        series=values,
        sufficient=len(series) >= required_history and current is not None,
        required_history=required_history,
    )


def ema(values: pd.Series, period: int) -> ScalarSeries:
    """EMA seeded with an SMA after `period` observations."""

    if period < 2:
        raise ValueError("EMA period must be at least 2")
    result = values.astype(float).ewm(span=period, adjust=False, min_periods=period).mean()
    return scalar_result(result, period)


def sma(values: pd.Series, period: int) -> ScalarSeries:
    if period < 2:
        raise ValueError("SMA period must be at least 2")
    return scalar_result(values.astype(float).rolling(period, min_periods=period).mean(), period)
