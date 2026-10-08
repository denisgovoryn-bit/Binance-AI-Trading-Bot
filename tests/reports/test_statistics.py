from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.reports.statistics import asset_group, calculate_metrics, confidence_bracket

NOW = datetime(2026, 8, 9, tzinfo=UTC)


def position(
    pnl: str,
    *,
    exchange="BINANCE",
    market="FUTURES",
    direction="LONG",
    symbol="BTCUSDT",
    confidence="87",
    timeframe="15m",
    status="COMPLETED",
):
    return SimpleNamespace(
        realized_pnl=Decimal(pnl),
        status=status,
        fees=Decimal("1"),
        exchange=exchange,
        market=market,
        direction=direction,
        symbol=symbol,
        confidence=Decimal(confidence),
        timeframe=timeframe,
        setup_type="LONG_SETUP",
        simulated_fill_price=Decimal("100"),
        initial_stop=Decimal("95"),
        initial_quantity=Decimal("2"),
        opened_at=NOW,
        closed_at=NOW + timedelta(hours=2) if status != "OPEN" else None,
    )


def test_daily_weekly_monthly_metric_inputs_and_segments() -> None:
    account = SimpleNamespace(
        starting_balance=Decimal("1000"),
        current_equity=Decimal("1030"),
        fees=Decimal("3"),
        max_drawdown=Decimal("-15"),
        current_drawdown=Decimal("-2"),
    )
    rows = [
        position("50"),
        position(
            "-20",
            exchange="BYBIT",
            market="SPOT",
            direction="BUY",
            symbol="ETHUSDT",
            confidence="92",
        ),
        position("0", symbol="SOLUSDT"),
        position("0", status="OPEN"),
    ]
    metrics = calculate_metrics(account, rows)
    assert metrics.closed_trades == 3 and metrics.open_positions == 1
    assert metrics.winning_trades == 1 and metrics.losing_trades == 1
    assert metrics.win_rate == Decimal("33.33")
    assert metrics.profit_factor == Decimal("2.5000")
    assert metrics.expectancy == Decimal("10.0000")
    assert metrics.net_pnl == Decimal("30") and metrics.return_pct == Decimal("3.00")
    assert metrics.segments["exchange"]["BINANCE"]["count"] == 2
    assert metrics.segments["market"]["SPOT"]["net_pnl"] == Decimal("-20")
    assert metrics.segments["confidence"]["90-94"]["count"] == 1


def test_asset_and_confidence_segmentation_boundaries() -> None:
    assert asset_group("BTCUSDT") == "BTC" and asset_group("ETHUSDT") == "ETH"
    assert asset_group("SOLUSDT") == "ALTCOINS"
    assert [confidence_bracket(Decimal(str(x))) for x in (79, 80, 85, 90, 95)] == [
        "70-79",
        "80-84",
        "85-89",
        "90-94",
        "95-100",
    ]
