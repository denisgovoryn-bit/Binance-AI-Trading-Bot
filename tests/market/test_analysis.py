from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.indicators.models import AnalysisConfig
from app.market.analysis import AnalysisService, DataQualityError
from app.market.models import Candle, ExchangeName, MarketType, Timeframe


def candles(count: int = 220) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    result = []
    for index in range(count):
        price = Decimal(100 + index) / Decimal(10)
        result.append(
            Candle(
                exchange=ExchangeName.BINANCE,
                market=MarketType.SPOT,
                symbol="BTCUSDT",
                timeframe=Timeframe.M1,
                open_time=start + timedelta(minutes=index),
                close_time=start + timedelta(minutes=index, seconds=59),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + Decimal("0.5"),
                volume=Decimal(100 + index),
            )
        )
    return result


def test_analysis_returns_explicit_degraded_state_for_short_history() -> None:
    analysis = AnalysisService().analyze_timeframe(candles(30))
    assert analysis.data_quality == "DEGRADED"
    assert analysis.indicators.ema[200].current is None
    assert analysis.warnings


def test_duplicate_and_out_of_order_candles_are_rejected() -> None:
    duplicate = candles(3)
    duplicate[2] = duplicate[1].model_copy()
    with pytest.raises(DataQualityError, match="duplicate"):
        AnalysisService().analyze_timeframe(duplicate)
    with pytest.raises(DataQualityError, match="chronological"):
        AnalysisService().analyze_timeframe(list(reversed(candles(3))))


def test_missing_candle_spacing_is_rejected() -> None:
    data = candles(3)
    data.pop(1)
    with pytest.raises(DataQualityError, match="spacing"):
        AnalysisService().analyze_timeframe(data)


def test_full_analysis_has_reproducible_current_features() -> None:
    config = AnalysisConfig(volatility_lookback=20)
    analysis = AnalysisService(config).analyze_timeframe(candles())
    assert analysis.data_quality == "VALID"
    assert analysis.indicators.ema[200].current is not None
    assert analysis.indicators.rsi.current == pytest.approx(100)
    assert analysis.indicators.atr.percentage.current is not None
    assert analysis.trend.reasons
    assert analysis.structure.reasons
