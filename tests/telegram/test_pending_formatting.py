from datetime import UTC
from types import SimpleNamespace

from app.telegram.formatting import format_pending
from tests.signals.conftest import NOW


def test_pending_format_uses_executable_quote_and_utc_expiry() -> None:
    record = SimpleNamespace(
        symbol="BTCUSDT",
        market="FUTURES",
        direction="LONG",
        current_confidence=85.0,
        current_monitored_price=100.0,
        current_bid_price=99.9,
        current_ask_price=100.1,
        current_executable_price=100.1,
        entry_low=99.0,
        entry_high=101.0,
        invalidation_price=95.0,
        targets=[],
        status="WAITING_ENTRY",
        expires_at=NOW.replace(tzinfo=None),
        entry_reason="support retest",
        payload={"supporting_metrics": {"atr": 2.0}, "risk_reward": {"best_r": 2.0}},
    )
    text = format_pending(record)
    assert "Last 100" in text
    assert "Bid 99.9" in text
    assert "Ask 100.1" in text
    assert "Executable quote: 100.1 ASK" in text
    assert NOW.replace(tzinfo=UTC).strftime("%Y-%m-%d %H:%M:%S UTC") in text
