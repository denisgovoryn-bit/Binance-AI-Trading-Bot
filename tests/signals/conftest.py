from datetime import UTC, datetime, timedelta

import pytest

from app.indicators.models import (
    ADXResult,
    ATRResult,
    BollingerResult,
    MACDResult,
    MomentumResult,
    ScalarSeries,
    TrendResult,
    TrendState,
    VolatilityRegime,
    VolatilityResult,
    VolumeResult,
    VolumeState,
)
from app.market.analysis import IndicatorSnapshot, MarketAnalysis, TimeframeAnalysis
from app.market.models import ExchangeName, MarketType, Timeframe
from app.market.structure import (
    BreakoutResult,
    BreakoutState,
    PriceZone,
    RangeResult,
    RetestResult,
    RetestState,
    StructureResult,
    StructureState,
    SwingKind,
    SwingLabel,
    SwingPoint,
    ZoneKind,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def scalar(value: float) -> ScalarSeries:
    return ScalarSeries(current=value, series=(value,) * 20, sufficient=True, required_history=1)


def zone(
    kind: ZoneKind, low: float, high: float, timeframe: Timeframe = Timeframe.M15
) -> PriceZone:
    return PriceZone(
        kind=kind,
        lower=low,
        upper=high,
        touches=3,
        first_seen=NOW - timedelta(days=2),
        last_seen=NOW - timedelta(hours=1),
        strength=80,
        timeframe=timeframe,
        broken=False,
    )


def make_timeframe(
    *,
    bullish: bool = True,
    timeframe: Timeframe = Timeframe.M15,
    data_quality: str = "VALID",
    current_price: float = 100,
    breakout_state: BreakoutState = BreakoutState.NONE,
    retest_state: RetestState = RetestState.NONE,
) -> TimeframeAnalysis:
    trend = TrendState.STRONG_BULLISH if bullish else TrendState.STRONG_BEARISH
    structure_state = StructureState.BULLISH if bullish else StructureState.BEARISH
    sign = 1 if bullish else -1
    macd_value = 1.0 * sign
    momentum = {1: 0.01 * sign, 3: 0.02 * sign, 5: 0.03 * sign, 10: 0.05 * sign}
    swings = (
        SwingPoint(
            index=10,
            timestamp=NOW - timedelta(hours=4),
            price=96 if bullish else 104,
            kind=SwingKind.LOW if bullish else SwingKind.HIGH,
            label=SwingLabel.UNCLASSIFIED,
            confirmed=True,
            confirmation_index=12,
        ),
        SwingPoint(
            index=20,
            timestamp=NOW - timedelta(hours=3),
            price=104 if bullish else 96,
            kind=SwingKind.HIGH if bullish else SwingKind.LOW,
            label=SwingLabel.UNCLASSIFIED,
            confirmed=True,
            confirmation_index=22,
        ),
        SwingPoint(
            index=30,
            timestamp=NOW - timedelta(hours=2),
            price=98 if bullish else 102,
            kind=SwingKind.LOW if bullish else SwingKind.HIGH,
            label=SwingLabel.HIGHER_LOW if bullish else SwingLabel.LOWER_HIGH,
            confirmed=True,
            confirmation_index=32,
        ),
        SwingPoint(
            index=40,
            timestamp=NOW - timedelta(hours=1),
            price=108 if bullish else 92,
            kind=SwingKind.HIGH if bullish else SwingKind.LOW,
            label=SwingLabel.HIGHER_HIGH if bullish else SwingLabel.LOWER_LOW,
            confirmed=True,
            confirmation_index=42,
        ),
    )
    zones = (
        (
            zone(ZoneKind.SUPPORT, 98, 99, timeframe),
            zone(ZoneKind.RESISTANCE, 101, 102, timeframe),
            zone(ZoneKind.RESISTANCE, 104, 105, timeframe),
            zone(ZoneKind.RESISTANCE, 108, 109, timeframe),
        )
        if bullish
        else (
            zone(ZoneKind.RESISTANCE, 101, 102, timeframe),
            zone(ZoneKind.SUPPORT, 98, 99, timeframe),
            zone(ZoneKind.SUPPORT, 95, 96, timeframe),
            zone(ZoneKind.SUPPORT, 91, 92, timeframe),
        )
    )
    breakout_zone = zone(
        ZoneKind.RESISTANCE if bullish else ZoneKind.SUPPORT,
        98.5 if bullish else 100.5,
        99.5 if bullish else 101.5,
        timeframe,
    )
    return TimeframeAnalysis(
        symbol="BTCUSDT",
        exchange=ExchangeName.BINANCE,
        market=MarketType.FUTURES,
        timeframe=timeframe,
        timestamp=NOW,
        current_price=current_price,
        data_quality=data_quality,
        warnings=() if data_quality == "VALID" else ("degraded fixture",),
        indicators=IndicatorSnapshot(
            ema={
                9: scalar(99 if bullish else 101),
                20: scalar(98 if bullish else 102),
                50: scalar(97 if bullish else 103),
                100: scalar(96 if bullish else 104),
                200: scalar(95 if bullish else 105),
            },
            rsi=scalar(60 if bullish else 40),
            macd=MACDResult(
                macd=scalar(macd_value),
                signal=scalar(macd_value / 2),
                histogram=scalar(macd_value / 2),
                histogram_change=0.1 * sign,
                histogram_direction="RISING" if bullish else "FALLING",
            ),
            atr=ATRResult(absolute=scalar(2), percentage=scalar(2)),
            adx=ADXResult(
                adx=scalar(30),
                plus_di=scalar(35 if bullish else 15),
                minus_di=scalar(15 if bullish else 35),
            ),
            rolling_vwap=scalar(99 if bullish else 101),
            utc_daily_vwap=scalar(99 if bullish else 101),
            bollinger=BollingerResult(
                middle=scalar(100),
                upper=scalar(104),
                lower=scalar(96),
                width_percentage=scalar(8),
                price_position=scalar(0.6 if bullish else 0.4),
            ),
            momentum=MomentumResult(
                returns=momentum, rate_of_change=scalar(5 * sign), acceleration=0.01 * sign
            ),
        ),
        trend=TrendResult(state=trend, reasons=(f"fixture trend {trend.value}",)),
        structure=StructureResult(
            state=structure_state,
            swings=swings,
            developing_swings=(),
            reasons=("confirmed swing sequence supports direction",),
        ),
        volatility=VolatilityResult(
            regime=VolatilityRegime.NORMAL,
            atr_percentage=2,
            percentile=50,
            reasons=("ATR% at median",),
        ),
        volume=VolumeResult(
            raw=200,
            sma=scalar(100),
            relative=scalar(2),
            z_score=scalar(2),
            state=VolumeState.EXPANSION,
            reasons=("volume is 2.00x baseline",),
        ),
        zones=zones,
        breakout=BreakoutResult(
            state=breakout_state,
            zone=breakout_zone if breakout_state is not BreakoutState.NONE else None,
            direction=("UP" if bullish else "DOWN")
            if breakout_state is not BreakoutState.NONE
            else None,
            atr_distance=0.25 if breakout_state is not BreakoutState.NONE else None,
            volume_confirmed=True if breakout_state is not BreakoutState.NONE else None,
            reasons=("fixture breakout",),
        ),
        retest=RetestResult(state=retest_state, reasons=("fixture retest",)),
        range=RangeResult(
            in_range=False,
            range_low=96,
            range_high=104,
            width_percentage=8,
            strength=0,
            reasons=("not ranging",),
        ),
    )


def make_market(
    *,
    bullish: bool = True,
    market_type: MarketType = MarketType.FUTURES,
    data_quality: str = "VALID",
    breakout_state: BreakoutState = BreakoutState.NONE,
    retest_state: RetestState = RetestState.NONE,
    current_price: float = 100,
) -> MarketAnalysis:
    timeframes = {
        timeframe: make_timeframe(
            bullish=bullish,
            timeframe=timeframe,
            data_quality=data_quality,
            breakout_state=breakout_state if timeframe is Timeframe.M15 else BreakoutState.NONE,
            retest_state=retest_state if timeframe is Timeframe.M15 else RetestState.NONE,
            current_price=current_price,
        )
        for timeframe in (Timeframe.M15, Timeframe.H1, Timeframe.H4)
    }
    for analysis in timeframes.values():
        analysis.market = market_type
    return MarketAnalysis(
        symbol="BTCUSDT",
        exchange=ExchangeName.BINANCE,
        market=market_type,
        timestamp=NOW,
        timeframes=timeframes,
    )


@pytest.fixture
def bullish_market() -> MarketAnalysis:
    return make_market()


@pytest.fixture
def bearish_market() -> MarketAnalysis:
    return make_market(bullish=False)
