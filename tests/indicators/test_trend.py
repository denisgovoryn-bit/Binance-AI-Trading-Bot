from app.indicators.models import ADXResult, ScalarSeries, TrendState
from app.indicators.trend import classify_trend


def series(current: float, history: tuple[float, ...]) -> ScalarSeries:
    return ScalarSeries(
        current=current,
        series=history,
        sufficient=True,
        required_history=len(history),
    )


def test_trend_requires_multiple_aligned_bullish_factors() -> None:
    adx = ADXResult(
        adx=series(30, (30,)),
        plus_di=series(35, (35,)),
        minus_di=series(15, (15,)),
    )
    result = classify_trend(
        price=120,
        ema_values={
            20: series(110, (100, 102, 104, 106, 108, 110)),
            50: series(105, (105,)),
        },
        adx_result=adx,
        structure_bias=1,
    )
    assert result.state is TrendState.STRONG_BULLISH
    assert "ADX at least 25 confirms directional strength" in result.reasons
    assert len(result.reasons) >= 4


def test_adx_does_not_create_direction_without_di_context() -> None:
    adx = ADXResult(
        adx=series(50, (50,)),
        plus_di=series(20, (20,)),
        minus_di=series(20, (20,)),
    )
    result = classify_trend(
        price=100,
        ema_values={20: series(100, (100,) * 6), 50: series(100, (100,))},
        adx_result=adx,
    )
    assert result.state is TrendState.NEUTRAL
