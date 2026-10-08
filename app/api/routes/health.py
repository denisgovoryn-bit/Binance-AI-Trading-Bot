"""Service health endpoint."""

from typing import Any

from fastapi import APIRouter, Request

from app.db.repositories.signals import ACTIVE_STATUSES, SignalRepository

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    database_ok = await request.app.state.database.healthy()
    active_count = 0
    stale_feed_count = 0
    if database_ok:
        async with request.app.state.database.sessions() as session:
            rows = await SignalRepository(session).list_signals(
                statuses=ACTIVE_STATUSES, limit=1000
            )
            active_count = len(rows)
            stale_feed_count = sum(row.monitoring_degraded for row in rows)
    manager = getattr(request.app.state, "monitoring_manager", None)
    monitoring = (
        manager.health()
        if manager is not None
        else {
            "enabled": request.app.state.settings.monitoring_enabled,
            "websocket_connection_state": "NOT_STARTED",
            "active_subscriptions": 0,
        }
    )
    monitoring["stale_feed_count"] = stale_feed_count
    private_health = request.app.state.account_service.health()
    scanner_state = getattr(request.app.state, "scanner_state", None)
    return {
        "status": "ok" if database_ok else "degraded",
        "database": "ok" if database_ok else "unavailable",
        "exchanges": {
            "binance_spot": "not_started",
            "binance_futures": "not_started",
            "bybit_spot": "not_started",
            "bybit_futures": "not_started",
            "binance_private": private_health["binance"],
            "bybit_private": private_health["bybit"],
        },
        "telegram": request.app.state.telegram_status,
        "trading_execution": "DISABLED",
        "scanner": "not_started",
        "scanners": scanner_state.safe_dict() if scanner_state is not None else {},
        "last_market_update": None,
        "active_signals": active_count,
        "monitoring": monitoring,
    }
