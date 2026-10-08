from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.config.settings import Settings
from app.db.directional_models import DirectionalScanCycleRecord
from app.db.repositories.directional_diagnostics import DirectionalDiagnosticsRepository
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.market.models import MarketType
from app.signals.diagnostics import categorize_reason, directional_diagnostics
from app.signals.engine import DirectionalSignalEngine
from app.signals.service import SignalService
from tests.signals.conftest import NOW, make_market


def test_engine_exposes_every_evaluated_direction_without_changing_selection() -> None:
    engine = DirectionalSignalEngine()
    futures = make_market(bullish=True, market_type=MarketType.FUTURES)
    candidates = engine.evaluate_candidates(futures, evaluated_at=NOW)
    assert {row.direction.value for row in candidates if row.direction} == {"LONG", "SHORT"}
    assert (
        engine.evaluate(futures, evaluated_at=NOW).direction
        == engine.select_candidate(futures, candidates).direction
    )
    spot = engine.evaluate_candidates(
        make_market(bullish=True, market_type=MarketType.SPOT), evaluated_at=NOW
    )
    assert len(spot) == 1 and spot[0].direction.value == "BUY"


@pytest.mark.asyncio
async def test_evaluation_audit_and_diagnostic_funnel(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'diagnostics.db'}")
    database = Database(settings)
    await database.create_schema()
    cycle_id = str(uuid4())
    now = datetime.now(UTC)
    async with database.sessions() as session:
        diagnostics_repo = DirectionalDiagnosticsRepository(session)
        await diagnostics_repo.create_cycle(
            DirectionalScanCycleRecord(
                id=cycle_id,
                started_at=now,
                completed_at=now,
                status="COMPLETED",
                expected_markets=1,
                evaluated_markets=1,
                failed_markets=0,
                expected_by_market={"BINANCE:FUTURES": 1},
                errors=[],
                duration_ms=10,
            )
        )
        market = make_market(bullish=True)
        market = market.model_copy(
            update={
                "timestamp": now,
                "timeframes": {
                    timeframe: analysis.model_copy(update={"timestamp": now})
                    for timeframe, analysis in market.timeframes.items()
                },
            }
        )
        outcome = await SignalService(SignalRepository(session), settings).evaluate(
            market, evaluated_at=now, cycle_id=cycle_id
        )
        assert outcome.signal.direction is not None
        report = await directional_diagnostics(session, hours=24)
        assert report["coverage"]["cycles"] == 1
        assert report["coverage"]["evaluated_markets"] == 1
        assert report["candidate_evaluations"]["total"] == 2
        assert report["candidate_evaluations"]["LONG"] == 1
        assert report["candidate_evaluations"]["SHORT"] == 1
        assert report["top_evaluations"][0]["components"]
    await database.close()


def test_rejection_reason_categories_are_deterministic() -> None:
    assert categorize_reason("best structural target offers 1.2R") == "poor_risk_reward"
    assert categorize_reason("higher timeframe conflict") == "htf_conflict"
    assert categorize_reason("spread exceeds execution limit") == "spread_execution_quality"
    assert categorize_reason("unmapped diagnostic") == "other"
