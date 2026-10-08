"""Read-only persisted signal API."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.db.repositories.portfolio import PortfolioRepository
from app.db.repositories.signals import ACTIVE_STATUSES, SignalRepository
from app.db.signal_models import SignalRecord
from app.signals.analytics import pending_metrics
from app.signals.diagnostics import directional_diagnostics
from app.signals.ranking import group_top
from app.signals.service import SignalService

router = APIRouter(prefix="/signals", tags=["signals"])


async def repository(request: Request) -> AsyncIterator[SignalRepository]:
    async with request.app.state.database.sessions() as session:
        yield SignalRepository(session)


Repo = Annotated[SignalRepository, Depends(repository)]


def serialize(record: SignalRecord, *, detailed: bool = False) -> dict[str, Any]:
    atr = record.payload.get("supporting_metrics", {}).get("atr")
    price = record.current_monitored_price or record.payload.get("supporting_metrics", {}).get(
        "current_price"
    )
    distance = _distance_atr(price, record.entry_low, record.entry_high, atr)
    risk_reward = record.payload.get("risk_reward") or {}
    result: dict[str, Any] = {
        "id": record.id,
        "fingerprint": record.fingerprint,
        "exchange": record.exchange,
        "market": record.market,
        "symbol": record.symbol,
        "direction": record.direction,
        "setup_type": record.setup_type,
        "timeframe": record.timeframe,
        "classification": record.classification,
        "status": record.status,
        "quote": {
            "last": record.current_monitored_price,
            "bid": record.current_bid_price,
            "ask": record.current_ask_price,
            "executable": record.current_executable_price,
            "reference": "ASK" if record.direction in {"BUY", "LONG"} else "BID",
            "last_market_event_at": record.last_market_event_at,
        },
        "confidence": record.current_confidence,
        "initial_confidence": record.initial_confidence,
        "maximum_confidence": record.maximum_confidence,
        "minimum_confidence": record.minimum_confidence,
        "created_at": record.created_at,
        "last_evaluated_at": record.last_evaluated_at,
        "expires_at": record.expires_at,
        "waiting_since": record.waiting_since,
        "expiry_reason": record.expiry_reason,
        "entry_zone": {
            "low": record.entry_low,
            "high": record.entry_high,
            "mid": record.ideal_entry,
            "basis": record.entry_basis,
            "reason": record.entry_reason,
        },
        "distance_to_entry_atr": distance,
        "invalidation_level": record.invalidation_price,
        "original_confidence": record.initial_confidence,
        "current_confidence": record.current_confidence,
        "confidence_delta": record.current_confidence - record.initial_confidence,
        "projected_risk_reward": record.payload.get(
            "risk_reward_at_entry", risk_reward.get("best_r")
        ),
        "entry_confirmed_at": record.entry_confirmed_at,
        "entry_price_reference": record.entry_price_reference,
        "current_monitored_price": record.current_monitored_price,
        "original_stop": record.original_invalidation_price,
        "current_stop": record.invalidation_price,
        "confidence_at_entry": record.confidence_at_entry,
        "max_confidence_after_entry": record.max_confidence_after_entry,
        "min_confidence_after_entry": record.min_confidence_after_entry,
        "monitoring_degraded": record.monitoring_degraded,
        "weakening_reasons": record.weakening_reasons,
    }
    if detailed:
        result["signal"] = record.payload
        result["history"] = [
            {
                "previous_status": x.previous_status,
                "status": x.new_status,
                "reason": x.reason,
                "at": x.at,
            }
            for x in record.lifecycle_events
        ]
        result["updates"] = [
            {
                "previous_confidence": x.previous_confidence,
                "current_confidence": x.current_confidence,
                "at": x.at,
                "reasons": x.reasons,
                "warnings": x.warnings,
            }
            for x in record.updates
        ]
        result["monitoring_events"] = serialize_events(record)
    return result


def _distance_atr(
    price: object, low: float | None, high: float | None, atr: object
) -> float | None:
    if not isinstance(price, (int, float)) or not isinstance(atr, (int, float)) or atr <= 0:
        return None
    if low is None or high is None:
        return None
    distance = low - price if price < low else price - high if price > high else 0
    return distance / atr


def serialize_events(record: SignalRecord) -> list[dict[str, Any]]:
    return [
        {
            "id": x.id,
            "event_type": x.event_type,
            "previous_status": x.previous_status,
            "new_status": x.new_status,
            "previous_confidence": x.previous_confidence,
            "current_confidence": x.current_confidence,
            "price": x.price,
            "configured_price": x.configured_price,
            "slippage": x.slippage,
            "reasons": x.reasons,
            "warnings": x.warnings,
            "analysis_timestamp": x.analysis_timestamp,
            "exchange_timestamp": x.exchange_timestamp,
            "received_at": x.received_at,
            "data_age_ms": x.data_age_ms,
            "metadata": x.metadata_json,
        }
        for x in record.monitoring_events
    ]


@router.get("")
async def list_signals(
    repo: Repo,
    exchange: str | None = None,
    market: str | None = None,
    symbol: str | None = None,
    direction: str | None = None,
    status: str | None = None,
    timeframe: str | None = None,
    min_confidence: float | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    rows = await repo.list_signals(
        statuses=[status.upper()] if status else None,
        exchange=exchange,
        market=market,
        symbol=symbol.upper() if symbol else None,
        direction=direction,
        timeframe=timeframe,
        min_confidence=min_confidence,
        offset=offset,
        limit=limit,
    )
    return {
        "count": len(rows),
        "offset": offset,
        "limit": limit,
        "signals": [serialize(row) for row in rows],
    }


@router.get("/latest")
async def latest(repo: Repo, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict[str, Any]:
    rows = await repo.list_signals(limit=limit)
    return {"count": len(rows), "signals": [serialize(row) for row in rows]}


@router.get("/active")
async def active(repo: Repo) -> dict[str, Any]:
    rows = await repo.list_signals(statuses=ACTIVE_STATUSES, limit=100)
    return {"count": len(rows), "signals": [serialize(row) for row in rows]}


@router.get("/watchlist")
async def watchlist(repo: Repo) -> dict[str, Any]:
    rows = await repo.list_signals(statuses=["WATCHLIST"], limit=100)
    return {"count": len(rows), "signals": [serialize(row) for row in rows]}


@router.get("/pending")
async def pending(request: Request, repo: Repo) -> dict[str, Any]:
    await SignalService(repo, request.app.state.settings).expire_due(datetime.now(UTC))
    rows = await repo.list_signals(statuses=["WAITING_ENTRY", "REVALIDATING"], limit=100)
    return {"count": len(rows), "signals": [serialize(row) for row in rows]}


@router.get("/pending/analytics")
async def pending_analytics(request: Request, repo: Repo) -> dict[str, object]:
    rows = await repo.list_signals(limit=10_000)
    async with request.app.state.database.sessions() as session:
        positions = await PortfolioRepository(session).all_positions()
    return pending_metrics(rows, positions)


@router.get("/diagnostics")
async def diagnostics(
    request: Request,
    window: str = "24h",
) -> dict[str, Any]:
    if not window.endswith("h") or not window[:-1].isdigit():
        raise HTTPException(status_code=422, detail="window must use hour syntax such as 24h")
    hours = int(window[:-1])
    if not 1 <= hours <= 168:
        raise HTTPException(status_code=422, detail="window must be between 1h and 168h")
    async with request.app.state.database.sessions() as session:
        return await directional_diagnostics(session, hours=hours)


@router.get("/top")
async def top(repo: Repo) -> dict[str, Any]:
    rows = await repo.list_signals(statuses=["WAITING_ENTRY"], min_confidence=80, limit=100)
    ranked = group_top(rows, 5)
    actionable = [
        item for item in ranked if serialize(item["signal"])["distance_to_entry_atr"] == 0
    ]
    pending_rows = [item for item in ranked if item not in actionable]
    if not ranked:
        return {
            "status": "NO_TRADE",
            "count": 0,
            "actionable_now": [],
            "pending_entry": [],
            "watchlist": [],
            "signals": [],
            "message": "No current setup meets configured quality requirements.",
        }
    return {
        "status": "QUALIFIED",
        "count": len(ranked),
        "actionable_now": [
            {
                **serialize(item["signal"]),
                "ranking_score": item["ranking_score"],
                "alternative_venues": item["alternative_venues"],
            }
            for item in actionable
        ],
        "pending_entry": [
            {
                **serialize(item["signal"]),
                "ranking_score": item["ranking_score"],
                "alternative_venues": item["alternative_venues"],
            }
            for item in pending_rows
        ],
        "watchlist": [],
        "signals": [serialize(item["signal"]) for item in ranked],
    }


@router.get("/monitoring")
async def monitoring(request: Request, repo: Repo) -> dict[str, Any]:
    rows = await repo.list_signals(statuses=ACTIVE_STATUSES, limit=1000)
    manager = getattr(request.app.state, "monitoring_manager", None)
    health = (
        manager.health()
        if manager is not None
        else {
            "enabled": request.app.state.settings.monitoring_enabled,
            "active_subscriptions": 0,
            "websocket_connection_state": "NOT_STARTED",
        }
    )
    return {**health, "monitored_signals": len(rows)}


async def required(signal_id: str, repo: SignalRepository) -> SignalRecord:
    row = await repo.get_signal(signal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="signal not found")
    return row


@router.get("/{signal_id}")
async def detail(signal_id: str, repo: Repo) -> dict[str, Any]:
    return serialize(await required(signal_id, repo), detailed=True)


@router.get("/{signal_id}/history")
async def history(signal_id: str, repo: Repo) -> dict[str, Any]:
    row = await required(signal_id, repo)
    return {"signal_id": signal_id, "history": serialize(row, detailed=True)["history"]}


@router.get("/{signal_id}/updates")
async def updates(signal_id: str, repo: Repo) -> dict[str, Any]:
    row = await required(signal_id, repo)
    return {"signal_id": signal_id, "updates": serialize(row, detailed=True)["updates"]}


@router.get("/{signal_id}/events")
async def events(signal_id: str, repo: Repo) -> dict[str, Any]:
    row = await required(signal_id, repo)
    return {
        "signal_id": signal_id,
        "count": len(row.monitoring_events),
        "events": serialize_events(row),
    }


@router.get("/{signal_id}/live")
async def live(signal_id: str, repo: Repo) -> dict[str, Any]:
    row = await required(signal_id, repo)
    return {
        "signal_id": row.id,
        "status": row.status,
        "current_monitored_price": row.current_monitored_price,
        "last_market_event_at": row.last_market_event_at,
        "entry_confirmed_at": row.entry_confirmed_at,
        "entry_price_reference": row.entry_price_reference,
        "original_stop": row.original_invalidation_price,
        "current_stop": row.invalidation_price,
        "targets_reached": [
            x.event_type for x in row.monitoring_events if x.event_type.startswith("TP")
        ],
        "confidence_at_entry": row.confidence_at_entry,
        "current_confidence": row.current_confidence,
        "weakening_reasons": row.weakening_reasons,
        "monitoring_degraded": row.monitoring_degraded,
    }
