"""Exchange-independent timeframe analysis models and service."""

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from pydantic import BaseModel

from app.config.settings import Settings
from app.indicators.models import (
    ADXResult,
    AnalysisConfig,
    ATRResult,
    BollingerResult,
    MACDResult,
    MomentumResult,
    ScalarSeries,
    TrendResult,
    VolatilityResult,
    VolumeResult,
)
from app.indicators.momentum import macd, price_momentum, rsi
from app.indicators.moving_averages import ema
from app.indicators.trend import classify_trend
from app.indicators.volatility import adx, atr, bollinger_bands, volatility_regime
from app.indicators.volume import rolling_vwap, utc_daily_vwap, volume_analysis
from app.market.models import Candle, ExchangeName, MarketType, Timeframe
from app.market.structure import (
    BreakoutResult,
    PriceZone,
    RangeResult,
    RetestResult,
    StructureResult,
    build_zones,
    classify_structure,
    detect_breakout,
    detect_range,
    detect_retest,
    detect_swings,
)


class DataQualityError(ValueError):
    """Candle input cannot safely support deterministic analysis."""


class IndicatorSnapshot(BaseModel):
    ema: dict[int, ScalarSeries]
    rsi: ScalarSeries
    macd: MACDResult
    atr: ATRResult
    adx: ADXResult
    rolling_vwap: ScalarSeries
    utc_daily_vwap: ScalarSeries
    bollinger: BollingerResult
    momentum: MomentumResult


class TimeframeAnalysis(BaseModel):
    symbol: str
    exchange: ExchangeName
    market: MarketType
    timeframe: Timeframe
    timestamp: datetime
    current_price: float
    data_quality: str
    warnings: tuple[str, ...]
    indicators: IndicatorSnapshot
    trend: TrendResult
    structure: StructureResult
    volatility: VolatilityResult
    volume: VolumeResult
    zones: tuple[PriceZone, ...]
    breakout: BreakoutResult
    retest: RetestResult
    range: RangeResult


class MarketAnalysis(BaseModel):
    symbol: str
    exchange: ExchangeName
    market: MarketType
    timestamp: datetime
    timeframes: dict[Timeframe, TimeframeAnalysis]


class AnalysisService:
    """Analyze candle snapshots without knowing their REST/WebSocket origin."""

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        self.config = config or AnalysisConfig()

    @classmethod
    def from_settings(cls, settings: Settings) -> "AnalysisService":
        """Map environment-backed settings into one validated analysis configuration."""

        return cls(
            AnalysisConfig(
                ema_periods=settings.ema_periods,
                rsi_period=settings.rsi_period,
                macd_fast=settings.macd_fast_period,
                macd_slow=settings.macd_slow_period,
                macd_signal=settings.macd_signal_period,
                atr_period=settings.atr_period,
                adx_period=settings.adx_period,
                bollinger_period=settings.bollinger_period,
                bollinger_stddev=settings.bollinger_stddev,
                volume_baseline=settings.volume_baseline,
                swing_window=settings.swing_window,
                zone_tolerance_atr=settings.zone_tolerance_atr,
                breakout_atr_threshold=settings.breakout_atr_threshold,
                breakout_persistence=settings.breakout_persistence,
                range_lookback=settings.range_lookback,
                range_max_width_atr=settings.range_max_width_atr,
                volatility_lookback=settings.volatility_lookback,
            )
        )

    def analyze_timeframe(self, candles: list[Candle]) -> TimeframeAnalysis:
        frame, metadata = candles_to_frame(candles)
        config = self.config
        closes = frame["close"]
        ema_values = {period: ema(closes, period) for period in config.ema_periods}
        rsi_result = rsi(closes, config.rsi_period)
        macd_result = macd(closes, config.macd_fast, config.macd_slow, config.macd_signal)
        atr_result = atr(frame, config.atr_period)
        adx_result = adx(frame, config.adx_period)
        bollinger = bollinger_bands(closes, config.bollinger_period, config.bollinger_stddev)
        volume = volume_analysis(frame["volume"], config.volume_baseline)
        swings, developing = detect_swings(frame, config.swing_window)
        structure = classify_structure(swings, developing)
        atr_value = atr_result.absolute.current
        current_price = float(closes.iloc[-1])
        zones = build_zones(
            swings, metadata["timeframe"], atr_value, current_price, config.zone_tolerance_atr
        )
        breakout = detect_breakout(
            frame,
            zones,
            atr_value,
            config.breakout_persistence,
            config.breakout_atr_threshold,
            volume.relative.current,
        )
        retest = detect_retest(frame, breakout)
        range_result = detect_range(
            frame,
            atr_value,
            adx_result.adx.current,
            config.range_lookback,
            config.range_max_width_atr,
        )
        warnings: list[str] = []
        required = max(config.ema_periods)
        if len(frame) < required:
            warnings.append(f"only {len(frame)} candles; EMA{required} requires {required}")
        quality = "DEGRADED" if warnings else "VALID"
        return TimeframeAnalysis(
            symbol=metadata["symbol"],
            exchange=metadata["exchange"],
            market=metadata["market"],
            timeframe=metadata["timeframe"],
            timestamp=candles[-1].close_time,
            current_price=current_price,
            data_quality=quality,
            warnings=tuple(warnings),
            indicators=IndicatorSnapshot(
                ema=ema_values,
                rsi=rsi_result,
                macd=macd_result,
                atr=atr_result,
                adx=adx_result,
                rolling_vwap=rolling_vwap(frame, config.volume_baseline),
                utc_daily_vwap=utc_daily_vwap(frame),
                bollinger=bollinger,
                momentum=price_momentum(closes),
            ),
            trend=classify_trend(current_price, ema_values, adx_result, structure.directional_bias),
            structure=structure,
            volatility=volatility_regime(atr_result.percentage.series, config.volatility_lookback),
            volume=volume,
            zones=zones,
            breakout=breakout,
            retest=retest,
            range=range_result,
        )

    def analyze_market(self, candles_by_timeframe: dict[Timeframe, list[Candle]]) -> MarketAnalysis:
        if not candles_by_timeframe:
            raise DataQualityError("at least one timeframe is required")
        analyses = {
            timeframe: self.analyze_timeframe(candles)
            for timeframe, candles in candles_by_timeframe.items()
        }
        first = next(iter(analyses.values()))
        for analysis in analyses.values():
            if (analysis.symbol, analysis.exchange, analysis.market) != (
                first.symbol,
                first.exchange,
                first.market,
            ):
                raise DataQualityError(
                    "all timeframes must describe the same exchange market symbol"
                )
        return MarketAnalysis(
            symbol=first.symbol,
            exchange=first.exchange,
            market=first.market,
            timestamp=max(item.timestamp for item in analyses.values()),
            timeframes=analyses,
        )


def candles_to_frame(candles: list[Candle]) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not candles:
        raise DataQualityError("candle series is empty")
    first = candles[0]
    identity = (first.exchange, first.market, first.symbol, first.timeframe)
    if any(
        (item.exchange, item.market, item.symbol, item.timeframe) != identity for item in candles
    ):
        raise DataQualityError("candle series contains mixed instruments or timeframes")
    times = [item.open_time for item in candles]
    if times != sorted(times):
        raise DataQualityError("candles must be chronological")
    if len(set(times)) != len(times):
        raise DataQualityError("duplicate candle timestamps")
    expected = _timeframe_delta(first.timeframe)
    if any(
        current - previous != expected for previous, current in zip(times, times[1:], strict=False)
    ):
        raise DataQualityError(f"unexpected candle spacing for {first.timeframe.value}")
    if any(item.volume < 0 for item in candles):
        raise DataQualityError("candle volume cannot be negative")
    frame = pd.DataFrame(
        {
            "open": [float(item.open) for item in candles],
            "high": [float(item.high) for item in candles],
            "low": [float(item.low) for item in candles],
            "close": [float(item.close) for item in candles],
            "volume": [float(item.volume) for item in candles],
        },
        index=pd.DatetimeIndex(times),
    )
    return frame, {
        "exchange": first.exchange,
        "market": first.market,
        "symbol": first.symbol,
        "timeframe": first.timeframe,
    }


def _timeframe_delta(timeframe: Timeframe) -> timedelta:
    return {
        Timeframe.M1: timedelta(minutes=1),
        Timeframe.M5: timedelta(minutes=5),
        Timeframe.M15: timedelta(minutes=15),
        Timeframe.H1: timedelta(hours=1),
        Timeframe.H4: timedelta(hours=4),
    }[timeframe]
