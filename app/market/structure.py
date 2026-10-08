"""Deterministic, lookahead-aware market structure analysis."""

from datetime import datetime
from enum import StrEnum
from typing import Any, cast

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from app.market.models import Timeframe


class SwingKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class SwingLabel(StrEnum):
    HIGHER_HIGH = "HIGHER_HIGH"
    LOWER_HIGH = "LOWER_HIGH"
    HIGHER_LOW = "HIGHER_LOW"
    LOWER_LOW = "LOWER_LOW"
    UNCLASSIFIED = "UNCLASSIFIED"


class SwingPoint(BaseModel):
    index: int
    timestamp: datetime
    price: float
    kind: SwingKind
    label: SwingLabel = SwingLabel.UNCLASSIFIED
    confirmed: bool
    confirmation_index: int | None = None


class StructureState(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    MIXED = "MIXED"
    INSUFFICIENT = "INSUFFICIENT"


class StructureResult(BaseModel):
    state: StructureState
    swings: tuple[SwingPoint, ...]
    developing_swings: tuple[SwingPoint, ...]
    reasons: tuple[str, ...]

    @property
    def directional_bias(self) -> int:
        return (
            1
            if self.state is StructureState.BULLISH
            else -1
            if self.state is StructureState.BEARISH
            else 0
        )


class ZoneKind(StrEnum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class PriceZone(BaseModel):
    kind: ZoneKind
    lower: float
    upper: float
    touches: int = Field(ge=1)
    first_seen: datetime
    last_seen: datetime
    strength: float = Field(ge=0, le=100)
    timeframe: Timeframe
    broken: bool
    midpoint: float = 0

    @model_validator(mode="after")
    def calculate_midpoint(self) -> "PriceZone":
        self.midpoint = (self.lower + self.upper) / 2
        return self


class BreakoutState(StrEnum):
    NONE = "NONE"
    ATTEMPTED = "ATTEMPTED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class BreakoutResult(BaseModel):
    state: BreakoutState
    zone: PriceZone | None = None
    direction: str | None = None
    atr_distance: float | None = None
    volume_confirmed: bool | None = None
    reasons: tuple[str, ...]


class RetestState(StrEnum):
    NONE = "NONE"
    PENDING = "PENDING"
    TOUCHED = "TOUCHED"
    HELD = "HELD"
    FAILED = "FAILED"


class RetestResult(BaseModel):
    state: RetestState
    reasons: tuple[str, ...]


class RangeResult(BaseModel):
    in_range: bool
    range_low: float | None
    range_high: float | None
    width_percentage: float | None
    strength: float
    reasons: tuple[str, ...]


def detect_swings(
    frame: pd.DataFrame, window: int = 2, include_developing: bool = True
) -> tuple[tuple[SwingPoint, ...], tuple[SwingPoint, ...]]:
    """Confirm pivots only after `window` later candles; recent candidates stay developing."""

    confirmed: list[SwingPoint] = []
    developing: list[SwingPoint] = []
    highs = frame["high"].astype(float)
    lows = frame["low"].astype(float)
    for index in range(window, len(frame)):
        timestamp = _timestamp(frame.index[index])
        left_highs = highs.iloc[index - window : index]
        left_lows = lows.iloc[index - window : index]
        has_future = index + window < len(frame)
        high_left = highs.iloc[index] > left_highs.max()
        low_left = lows.iloc[index] < left_lows.min()
        if has_future:
            right_highs = highs.iloc[index + 1 : index + window + 1]
            right_lows = lows.iloc[index + 1 : index + window + 1]
            if high_left and highs.iloc[index] > right_highs.max():
                confirmed.append(
                    SwingPoint(
                        index=index,
                        timestamp=timestamp,
                        price=highs.iloc[index],
                        kind=SwingKind.HIGH,
                        confirmed=True,
                        confirmation_index=index + window,
                    )
                )
            if low_left and lows.iloc[index] < right_lows.min():
                confirmed.append(
                    SwingPoint(
                        index=index,
                        timestamp=timestamp,
                        price=lows.iloc[index],
                        kind=SwingKind.LOW,
                        confirmed=True,
                        confirmation_index=index + window,
                    )
                )
        elif include_developing:
            if high_left:
                developing.append(
                    SwingPoint(
                        index=index,
                        timestamp=timestamp,
                        price=highs.iloc[index],
                        kind=SwingKind.HIGH,
                        confirmed=False,
                    )
                )
            if low_left:
                developing.append(
                    SwingPoint(
                        index=index,
                        timestamp=timestamp,
                        price=lows.iloc[index],
                        kind=SwingKind.LOW,
                        confirmed=False,
                    )
                )
    return tuple(_label_swings(confirmed)), tuple(developing)


def classify_structure(
    swings: tuple[SwingPoint, ...], developing: tuple[SwingPoint, ...] = ()
) -> StructureResult:
    highs = [point for point in swings if point.kind is SwingKind.HIGH]
    lows = [point for point in swings if point.kind is SwingKind.LOW]
    if len(highs) < 2 or len(lows) < 2:
        return StructureResult(
            state=StructureState.INSUFFICIENT,
            swings=swings,
            developing_swings=developing,
            reasons=("requires at least two confirmed swing highs and two confirmed swing lows",),
        )
    high_label = highs[-1].label
    low_label = lows[-1].label
    reasons: tuple[str, ...]
    if high_label is SwingLabel.HIGHER_HIGH and low_label is SwingLabel.HIGHER_LOW:
        state = StructureState.BULLISH
        reasons = (
            "latest confirmed swing high exceeded previous swing high",
            "latest confirmed swing low remained above previous swing low",
        )
    elif high_label is SwingLabel.LOWER_HIGH and low_label is SwingLabel.LOWER_LOW:
        state = StructureState.BEARISH
        reasons = (
            "latest confirmed swing high remained below previous swing high",
            "latest confirmed swing low broke below previous swing low",
        )
    else:
        state = StructureState.MIXED
        reasons = ("confirmed swing high and low sequences are not directionally aligned",)
    return StructureResult(
        state=state, swings=swings, developing_swings=developing, reasons=reasons
    )


def build_zones(
    swings: tuple[SwingPoint, ...],
    timeframe: Timeframe,
    atr_value: float | None,
    current_price: float,
    tolerance_atr: float = 0.5,
) -> tuple[PriceZone, ...]:
    if not swings or atr_value is None or atr_value <= 0:
        return ()
    tolerance = atr_value * tolerance_atr
    zones: list[PriceZone] = []
    for kind, zone_kind in (
        (SwingKind.LOW, ZoneKind.SUPPORT),
        (SwingKind.HIGH, ZoneKind.RESISTANCE),
    ):
        groups: list[list[SwingPoint]] = []
        for point in sorted((s for s in swings if s.kind is kind), key=lambda s: s.price):
            matching = next(
                (
                    group
                    for group in groups
                    if abs(sum(p.price for p in group) / len(group) - point.price) <= tolerance
                ),
                None,
            )
            if matching is None:
                groups.append([point])
            else:
                matching.append(point)
        for group in groups:
            prices = [point.price for point in group]
            midpoint = sum(prices) / len(prices)
            lower, upper = midpoint - tolerance / 2, midpoint + tolerance / 2
            recency = max(point.index for point in group) / max(swings[-1].index, 1)
            strength = min(100.0, len(group) * 20 + recency * 20)
            broken = (
                current_price < lower if zone_kind is ZoneKind.SUPPORT else current_price > upper
            )
            zones.append(
                PriceZone(
                    kind=zone_kind,
                    lower=lower,
                    upper=upper,
                    touches=len(group),
                    first_seen=min(point.timestamp for point in group),
                    last_seen=max(point.timestamp for point in group),
                    strength=strength,
                    timeframe=timeframe,
                    broken=broken,
                )
            )
    return tuple(sorted(zones, key=lambda zone: zone.midpoint))


def detect_breakout(
    frame: pd.DataFrame,
    zones: tuple[PriceZone, ...],
    atr_value: float | None,
    persistence: int = 2,
    atr_threshold: float = 0.1,
    relative_volume: float | None = None,
) -> BreakoutResult:
    if not zones or atr_value is None or atr_value <= 0:
        return BreakoutResult(state=BreakoutState.NONE, reasons=("no eligible zone or ATR value",))
    latest = frame.iloc[-1]
    for zone in sorted(zones, key=lambda item: abs(item.midpoint - float(latest["close"]))):
        boundary = zone.upper if zone.kind is ZoneKind.RESISTANCE else zone.lower
        direction = "UP" if zone.kind is ZoneKind.RESISTANCE else "DOWN"
        closes = frame["close"].astype(float).iloc[-persistence:]
        beyond = (
            closes > boundary + atr_value * atr_threshold
            if direction == "UP"
            else closes < boundary - atr_value * atr_threshold
        )
        wick_beyond = (
            float(latest["high"]) > boundary
            if direction == "UP"
            else float(latest["low"]) < boundary
        )
        close_beyond = (
            float(latest["close"]) > boundary
            if direction == "UP"
            else float(latest["close"]) < boundary
        )
        distance = abs(float(latest["close"]) - boundary) / atr_value
        volume_confirmed = None if relative_volume is None else relative_volume >= 1.2
        if len(closes) == persistence and bool(beyond.all()):
            return BreakoutResult(
                state=BreakoutState.CONFIRMED,
                zone=zone,
                direction=direction,
                atr_distance=distance,
                volume_confirmed=volume_confirmed,
                reasons=(
                    f"{persistence} closes persisted beyond the zone by at least "
                    f"{atr_threshold:.2f} ATR",
                ),
            )
        if wick_beyond and not close_beyond:
            return BreakoutResult(
                state=BreakoutState.FAILED,
                zone=zone,
                direction=direction,
                atr_distance=distance,
                volume_confirmed=volume_confirmed,
                reasons=("price traded beyond the zone but closed back inside",),
            )
        if close_beyond:
            return BreakoutResult(
                state=BreakoutState.ATTEMPTED,
                zone=zone,
                direction=direction,
                atr_distance=distance,
                volume_confirmed=volume_confirmed,
                reasons=("latest close is beyond the zone but persistence is not confirmed",),
            )
    return BreakoutResult(
        state=BreakoutState.NONE, reasons=("price remains within established zones",)
    )


def detect_retest(frame: pd.DataFrame, breakout: BreakoutResult) -> RetestResult:
    if breakout.state is not BreakoutState.CONFIRMED or breakout.zone is None:
        return RetestResult(state=RetestState.NONE, reasons=("no confirmed breakout to retest",))
    zone = breakout.zone
    latest = frame.iloc[-1]
    touched = float(latest["low"]) <= zone.upper and float(latest["high"]) >= zone.lower
    if not touched:
        return RetestResult(
            state=RetestState.PENDING, reasons=("price has not returned to the broken zone",)
        )
    if breakout.direction == "UP":
        held = float(latest["close"]) >= zone.upper
    else:
        held = float(latest["close"]) <= zone.lower
    return RetestResult(
        state=RetestState.HELD if held else RetestState.FAILED,
        reasons=(
            (
                "retest touched the zone and closed on the breakout side"
                if held
                else "retest closed through the broken zone"
            ),
        ),
    )


def detect_range(
    frame: pd.DataFrame,
    atr_value: float | None,
    adx_value: float | None,
    lookback: int = 20,
    maximum_width_atr: float = 6,
) -> RangeResult:
    if len(frame) < lookback or atr_value is None or atr_value <= 0:
        return RangeResult(
            in_range=False,
            range_low=None,
            range_high=None,
            width_percentage=None,
            strength=0,
            reasons=(f"requires {lookback} candles and a valid ATR",),
        )
    recent = frame.iloc[-lookback:]
    low, high = float(recent["low"].min()), float(recent["high"].max())
    width_atr = (high - low) / atr_value
    width_pct = (high - low) / ((high + low) / 2) * 100
    low_directionality = adx_value is not None and adx_value < 25
    in_range = width_atr <= maximum_width_atr and low_directionality
    width_score = max(0.0, 1 - width_atr / maximum_width_atr) * 60
    adx_score = max(0.0, (25 - (adx_value or 25)) / 25) * 40
    return RangeResult(
        in_range=in_range,
        range_low=low,
        range_high=high,
        width_percentage=width_pct,
        strength=width_score + adx_score if in_range else 0,
        reasons=(
            f"{lookback}-candle width is {width_atr:.2f} ATR",
            f"ADX is {adx_value:.2f}" if adx_value is not None else "ADX unavailable",
        ),
    )


def _label_swings(swings: list[SwingPoint]) -> list[SwingPoint]:
    previous: dict[SwingKind, SwingPoint] = {}
    result: list[SwingPoint] = []
    for point in sorted(swings, key=lambda item: item.index):
        prior = previous.get(point.kind)
        if prior is None:
            label = SwingLabel.UNCLASSIFIED
        elif point.kind is SwingKind.HIGH:
            label = SwingLabel.HIGHER_HIGH if point.price > prior.price else SwingLabel.LOWER_HIGH
        else:
            label = SwingLabel.HIGHER_LOW if point.price > prior.price else SwingLabel.LOWER_LOW
        updated = point.model_copy(update={"label": label})
        previous[point.kind] = updated
        result.append(updated)
    return result


def _timestamp(value: object) -> datetime:
    timestamp = pd.Timestamp(cast(Any, value))
    result = timestamp.to_pydatetime()
    if not isinstance(result, datetime):
        raise TypeError("timestamp did not convert to datetime")
    return result
