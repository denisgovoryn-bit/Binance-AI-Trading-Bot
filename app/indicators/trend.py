"""Explainable multi-factor trend classification."""

from app.indicators.models import ADXResult, ScalarSeries, TrendResult, TrendState


def classify_trend(
    price: float,
    ema_values: dict[int, ScalarSeries],
    adx_result: ADXResult,
    structure_bias: int = 0,
) -> TrendResult:
    """Classify trend from EMA position/order/slope, DI context, and swing bias.

    Each bullish observation adds one point and each bearish observation subtracts one.
    Strong states require at least four aligned factors; ordinary states require two.
    ADX never supplies direction—it only upgrades an already directional DI observation when
    ADX is at least 25, the conventional trend-strength reference level.
    """

    score = 0
    reasons: list[str] = []
    ema20 = ema_values.get(20)
    ema50 = ema_values.get(50)
    if ema20 and ema50 and ema20.current is not None and ema50.current is not None:
        if price > ema20.current and price > ema50.current:
            score += 1
            reasons.append("price above EMA20 and EMA50")
        elif price < ema20.current and price < ema50.current:
            score -= 1
            reasons.append("price below EMA20 and EMA50")
        if ema20.current > ema50.current:
            score += 1
            reasons.append("EMA20 above EMA50")
        elif ema20.current < ema50.current:
            score -= 1
            reasons.append("EMA20 below EMA50")
        historical = [value for value in ema20.series if value is not None]
        if len(historical) >= 6:
            slope = historical[-1] - historical[-6]
            if slope > 0:
                score += 1
                reasons.append("EMA20 five-candle slope positive")
            elif slope < 0:
                score -= 1
                reasons.append("EMA20 five-candle slope negative")
    plus_di = adx_result.plus_di.current
    minus_di = adx_result.minus_di.current
    adx_value = adx_result.adx.current
    if plus_di is not None and minus_di is not None:
        directional = 1 if plus_di > minus_di else -1 if minus_di > plus_di else 0
        if directional:
            score += directional
            reasons.append("+DI above -DI" if directional > 0 else "-DI above +DI")
            if adx_value is not None and adx_value >= 25:
                score += directional
                reasons.append("ADX at least 25 confirms directional strength")
    if structure_bias:
        score += 1 if structure_bias > 0 else -1
        reasons.append(
            "confirmed swings bullish" if structure_bias > 0 else "confirmed swings bearish"
        )
    state = (
        TrendState.STRONG_BULLISH
        if score >= 4
        else TrendState.BULLISH
        if score >= 2
        else TrendState.STRONG_BEARISH
        if score <= -4
        else TrendState.BEARISH
        if score <= -2
        else TrendState.NEUTRAL
    )
    if not reasons:
        reasons.append("insufficient aligned trend factors")
    return TrendResult(state=state, reasons=tuple(reasons))
