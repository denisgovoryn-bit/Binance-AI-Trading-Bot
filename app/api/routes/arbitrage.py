"""Read-only arbitrage opportunity API."""

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.db.arbitrage_models import ArbitrageOpportunityRecord
from app.db.repositories.arbitrage import ACTIVE_ARBITRAGE_STATUSES, ArbitrageRepository

router = APIRouter(prefix="/arbitrage", tags=["arbitrage"])


async def repository(request: Request) -> AsyncIterator[ArbitrageRepository]:
    async with request.app.state.database.sessions() as session:
        yield ArbitrageRepository(session)


Repo = Annotated[ArbitrageRepository, Depends(repository)]


def serialize(row: ArbitrageOpportunityRecord, detailed: bool = False) -> dict[str, Any]:
    result = {
        "id": row.id,
        "type": row.arbitrage_type,
        "symbol": row.symbol,
        "status": row.status,
        "classification": row.classification,
        "confidence": row.confidence_score,
        "buy": {"exchange": row.buy_exchange, "market": row.buy_market},
        "sell": {"exchange": row.sell_exchange, "market": row.sell_market},
        "gross_spread_pct": row.gross_spread_pct,
        "net_edge_pct": row.net_edge_pct,
        "expected_net_pnl": row.expected_net_pnl,
        "target_notional": row.target_notional,
        "detected_at": row.detected_at,
        "last_updated_at": row.last_updated_at,
    }
    if detailed:
        result["payload"] = row.payload
        result["history"] = [
            {
                "previous_status": event.previous_status,
                "status": event.new_status,
                "reason": event.reason,
                "at": event.at,
            }
            for event in row.events
        ]
        result["updates"] = [
            {
                "previous_net_edge_pct": update.previous_net_edge_pct,
                "current_net_edge_pct": update.current_net_edge_pct,
                "confidence": update.confidence_score,
                "at": update.at,
            }
            for update in row.updates
        ]
    return result


@router.get("")
async def list_opportunities(
    repo: Repo,
    symbol: str | None = None,
    arbitrage_type: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    rows = await repo.list(symbol=symbol, arbitrage_type=arbitrage_type, offset=offset, limit=limit)
    return {"count": len(rows), "opportunities": [serialize(row) for row in rows]}


@router.get("/latest")
async def latest(repo: Repo) -> dict[str, Any]:
    rows = await repo.list(limit=20)
    return {"count": len(rows), "opportunities": [serialize(row) for row in rows]}


@router.get("/active")
async def active(repo: Repo) -> dict[str, Any]:
    rows = await repo.list(statuses=ACTIVE_ARBITRAGE_STATUSES, limit=100)
    return {"count": len(rows), "opportunities": [serialize(row) for row in rows]}


@router.get("/top")
async def top(repo: Repo) -> dict[str, Any]:
    rows = await repo.list(statuses=("QUALIFIED",), limit=100)
    rows.sort(key=lambda row: (-row.confidence_score, -float(row.net_edge_pct), row.id))
    selected = rows[:5]
    return {
        "status": "QUALIFIED" if selected else "NO_ARBITRAGE",
        "count": len(selected),
        "opportunities": [serialize(row) for row in selected],
        "message": None
        if selected
        else "No current arbitrage opportunity meets configured quality requirements.",
    }


@router.get("/stats")
async def stats(repo: Repo) -> dict[str, Any]:
    rows = await repo.list(limit=1000)
    qualified = [row for row in rows if row.status in {"QUALIFIED", "PAPER_ACTIVE", "CLOSED"}]
    average_gross = (
        sum((float(row.gross_spread_pct) for row in qualified), 0.0) / len(qualified)
        if qualified
        else 0.0
    )
    average_net = (
        sum((float(row.net_edge_pct) for row in qualified), 0.0) / len(qualified)
        if qualified
        else 0.0
    )
    return {
        "opportunities": len(rows),
        "qualified": len(qualified),
        "average_gross_edge_pct": average_gross,
        "average_net_edge_pct": average_net,
        "paper_only": True,
    }


@router.get("/{opportunity_id}")
async def detail(opportunity_id: str, repo: Repo) -> dict[str, Any]:
    row = await repo.get(opportunity_id)
    if row is None:
        raise HTTPException(404, "arbitrage opportunity not found")
    return serialize(row, True)


@router.get("/{opportunity_id}/history")
async def history(opportunity_id: str, repo: Repo) -> dict[str, Any]:
    row = await repo.get(opportunity_id)
    if row is None:
        raise HTTPException(404, "arbitrage opportunity not found")
    return {"opportunity_id": opportunity_id, "history": serialize(row, True)["history"]}
