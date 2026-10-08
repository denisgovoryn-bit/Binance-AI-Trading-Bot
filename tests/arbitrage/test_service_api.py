from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.arbitrage.models import ArbitrageEconomics
from app.arbitrage.scanner import ArbitrageCandidate
from app.arbitrage.service import ArbitrageService
from app.config.settings import Settings
from app.db.repositories.arbitrage import ArbitrageRepository
from app.db.session import Database
from app.main import create_app
from app.market.models import ExchangeName, MarketType

NOW = datetime(2026, 8, 9, tzinfo=UTC)
D = Decimal


def candidate(edge: str = "0.5") -> ArbitrageCandidate:
    economics = ArbitrageEconomics(
        target_notional=D("500"),
        executable_notional=D("500"),
        gross_spread_pct=D("0.72"),
        fees_pct=D("0.20"),
        slippage_pct=D("0"),
        safety_buffer_pct=D("0.02"),
        expected_net_edge_pct=D(edge),
        expected_net_pnl=D("500") * D(edge) / D("100"),
    )
    return ArbitrageCandidate(
        "BTCUSDT",
        MarketType.SPOT,
        ExchangeName.BINANCE,
        ExchangeName.BYBIT,
        economics,
        D("10"),
        D("10"),
        D("0"),
    )


@pytest.mark.asyncio
async def test_dedup_persistence_qualification_and_invalidation(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/arb.db",
        arbitrage_min_consecutive_updates=3,
        arbitrage_min_persistence_ms=300,
    )
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        repo = ArbitrageRepository(session)
        service = ArbitrageService(repo, settings)
        first, created = await service.process(candidate(), NOW)
        second, duplicate = await service.process(
            candidate("0.55"), NOW + timedelta(milliseconds=200)
        )
        third, _ = await service.process(candidate("0.60"), NOW + timedelta(milliseconds=400))
        assert created and not duplicate and first.id == second.id == third.id
        assert third.status == "QUALIFIED" and third.consecutive_updates == 3
        assert len(third.events) == 3 and len(third.updates) == 2
        await service.invalidate(third, NOW + timedelta(seconds=1), "net edge collapsed")
        await service.invalidate(third, NOW + timedelta(seconds=2), "duplicate")
        assert third.status == "INVALIDATED" and len(third.events) == 4
    await database.close()


@pytest.mark.asyncio
async def test_sqlite_naive_timestamp_is_normalized_on_live_update(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/timezone.db")
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        await ArbitrageService(ArbitrageRepository(session), settings).process(candidate(), NOW)
    async with database.sessions() as session:
        updated, created = await ArbitrageService(ArbitrageRepository(session), settings).process(
            candidate("0.55"), NOW + timedelta(seconds=1)
        )
        assert not created
        assert updated.consecutive_updates == 2
    await database.close()


def test_read_only_api_empty_top_and_detail(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/api.db")
    with TestClient(create_app(settings)) as client:
        top = client.get("/arbitrage/top")
        assert top.status_code == 200 and top.json()["status"] == "NO_ARBITRAGE"
        assert client.get("/arbitrage/missing").status_code == 404
