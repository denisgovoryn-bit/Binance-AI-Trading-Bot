import asyncio
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.db.repositories.portfolio import PortfolioRepository
from app.db.session import Database
from app.portfolio.fees import apply_slippage, calculate_fee, fee_bps
from app.portfolio.models import PaperProfile, parse_balance
from app.portfolio.sizing import size_position
from app.portfolio.user_service import UserService


def test_balance_validation_and_decimal_sizing() -> None:
    assert parse_balance("1000 USDT") == Decimal("1000.00")
    for invalid in ("nope", "0", "NaN", "1000000001"):
        with pytest.raises(ValueError):
            parse_balance(invalid)
    settings = Settings()
    conservative = size_position(
        Decimal("1000"), Decimal("100"), PaperProfile.CONSERVATIVE, "SPOT", settings
    )
    balanced = size_position(
        Decimal("1000"), Decimal("100"), PaperProfile.BALANCED, "FUTURES", settings
    )
    aggressive = size_position(
        Decimal("1000"), Decimal("100"), PaperProfile.AGGRESSIVE, "FUTURES", settings
    )
    assert conservative.notional == Decimal("50.00000000")
    assert balanced.notional == Decimal("100.00000000")
    assert aggressive.notional == Decimal("200.00000000")
    assert balanced.leverage == Decimal("1.0")
    assert balanced.quantity > 0


def test_configurable_fee_and_slippage_models() -> None:
    settings = Settings()
    assert fee_bps("BINANCE", "SPOT", settings) == Decimal("10")
    assert fee_bps("BYBIT", "FUTURES", settings) == Decimal("5.5")
    assert apply_slippage(Decimal("100"), True, True, settings) == Decimal("100.02000000")
    assert apply_slippage(Decimal("100"), False, True, settings) == Decimal("99.98000000")
    assert calculate_fee(Decimal("100"), Decimal("2"), Decimal("10")) == Decimal("0.20000000")


@pytest.mark.asyncio
async def test_user_onboarding_isolation_and_explicit_reset(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        repo = PortfolioRepository(session)
        service = UserService(repo)
        first, created = await service.get_or_create(1001, "alice", "Alice")
        assert created and not first.onboarding_completed
        same, created = await service.get_or_create(1001)
        assert not created and same.id == first.id
        second, _ = await service.get_or_create(2002, "bob", "Bob")
        account1 = await service.complete_onboarding(first, "1000 USDT", ["FUTURES"])
        account2 = await service.complete_onboarding(second, "2500", ["SPOT"])
        assert account1.starting_balance == Decimal("1000.00")
        assert account2.starting_balance == Decimal("2500.00")
        assert first.preferences.enabled_markets == ["FUTURES"]
        replacement = await service.reset_account(first, "1200")
        assert not account1.active and account1.archived_at is not None
        assert replacement.active and replacement.id != account1.id
        assert (await repo.active_account(second.id)).id == account2.id  # type: ignore[union-attr]
    await database.close()


@pytest.mark.asyncio
async def test_concurrent_duplicate_start_creates_one_user(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
    database = Database(settings)
    await database.create_schema()

    async def create() -> tuple[int, bool]:
        async with database.sessions() as session:
            user, created = await UserService(PortfolioRepository(session)).get_or_create(1001)
            return user.id, created

    results = await asyncio.gather(create(), create())
    assert results[0][0] == results[1][0]
    assert sum(created for _, created in results) == 1
    await database.close()
