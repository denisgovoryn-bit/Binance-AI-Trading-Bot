from decimal import Decimal
from types import SimpleNamespace

from app.config.settings import Settings
from app.db.session import Database
from app.telegram.bot import create_dispatcher
from app.telegram.formatting import (
    DISCLAIMER,
    format_directional_stats,
    format_near_misses,
    format_portfolio,
    format_top,
    money,
    number,
    welcome_text,
)


def test_onboarding_no_trade_and_financial_formatting() -> None:
    assert "Virtual / Paper Balance" in welcome_text("<Alice>")
    assert "&lt;Alice&gt;" in welcome_text("<Alice>")
    no_trade = format_top([])
    assert "NO TRADE" in no_trade and "minimum quality" in no_trade
    assert money(Decimal("1000")) == "1,000.00"
    assert number("100.120000") == "100.12"
    assert "not win probability" in DISCLAIMER


def test_portfolio_is_clearly_virtual() -> None:
    account = SimpleNamespace(
        starting_balance=Decimal("1000"),
        current_cash=Decimal("1010"),
        current_equity=Decimal("1015"),
        realized_pnl=Decimal("10"),
        unrealized_pnl=Decimal("5"),
        fees=Decimal("1"),
    )
    text = format_portfolio(account, [])
    assert "PAPER PORTFOLIO" in text
    assert "Virtual starting balance" in text
    assert "No exchange balance" in text


def test_dispatcher_builds_primary_ui_without_token(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram.db'}")
    dispatcher = create_dispatcher(Database(settings), settings)
    assert any(router.name == "primary-telegram-ui" for router in dispatcher.sub_routers)


def test_directional_diagnostics_are_explicitly_observational() -> None:
    report = {
        "coverage": {"cycles": 1, "evaluated_markets": 10},
        "funnel": {
            "ACTIONABLE": 0,
            "PENDING_ENTRY": 0,
            "WATCHLIST": 0,
            "NO_TRADE": 10,
        },
        "scores": {"gte_80": 1, "gte_85": 0, "gte_90": 0},
        "rejections": {"primary": {"poor_risk_reward": 3}},
        "top_evaluations": [
            {
                "symbol": "BTCUSDT",
                "market": "FUTURES",
                "direction": "LONG",
                "score": 82.0,
                "primary_rejection_reason": "poor R:R",
            }
        ],
        "near_misses": [],
        "audit_available": True,
    }
    assert "Trading Execution: 🔒 DISABLED" in format_directional_stats(report)
    assert "NOT A SIGNAL — NOT AN ENTRY RECOMMENDATION" in format_near_misses([])
