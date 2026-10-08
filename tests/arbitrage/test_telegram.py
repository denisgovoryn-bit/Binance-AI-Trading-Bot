from datetime import UTC, datetime
from decimal import Decimal

from app.db.arbitrage_models import ArbitrageOpportunityRecord
from app.telegram.formatting import format_arbitrage, format_arbitrage_top


def opportunity() -> ArbitrageOpportunityRecord:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    return ArbitrageOpportunityRecord(
        id="arb-1",
        fingerprint="f",
        arbitrage_type="CROSS_EXCHANGE_SPOT",
        symbol="BTCUSDT",
        buy_exchange="BINANCE",
        buy_market="SPOT",
        sell_exchange="BYBIT",
        sell_market="SPOT",
        status="QUALIFIED",
        classification="STRONG_OPPORTUNITY",
        detected_at=now,
        last_updated_at=now,
        consecutive_updates=3,
        confidence_score=86,
        gross_spread_pct=Decimal("0.362"),
        net_edge_pct=Decimal("0.146"),
        expected_net_pnl=Decimal("0.73"),
        target_notional=Decimal("500"),
        payload={
            "economics": {
                "buy_vwap": "117420",
                "sell_vwap": "117845",
                "fees_pct": "0.155",
                "slippage_pct": "0.041",
                "safety_buffer_pct": "0.020",
            }
        },
    )


def test_qualified_and_no_arbitrage_formatting() -> None:
    text = format_arbitrage(opportunity())
    assert "Executable" not in text or "ARBITRAGE" in text
    assert "0.146%" in text and "No real orders" in text
    assert "NO ARBITRAGE" in format_arbitrage_top([])
    assert "#1" in format_arbitrage_top([opportunity()])
