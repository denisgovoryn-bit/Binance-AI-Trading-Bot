"""Admin-protected read-only paper portfolio and report endpoints."""

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.db.portfolio_models import PaperAccountRecord
from app.db.repositories.portfolio import PortfolioRepository
from app.reports.service import ReportService

router = APIRouter(prefix="/users", tags=["paper-portfolio"])


async def repository(
    request: Request, x_admin_id: Annotated[int | None, Header()] = None
) -> AsyncIterator[PortfolioRepository]:
    admins = request.app.state.settings.telegram_admin_ids
    if admins and x_admin_id not in admins:
        raise HTTPException(status_code=403, detail="admin access required")
    async with request.app.state.database.sessions() as session:
        yield PortfolioRepository(session)


Repo = Annotated[PortfolioRepository, Depends(repository)]


async def account(user_id: int, repo: PortfolioRepository) -> PaperAccountRecord:
    found = await repo.active_account(user_id)
    if found is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    return found


@router.get("/{user_id}/portfolio")
async def portfolio(user_id: int, repo: Repo) -> dict[str, Any]:
    item = await account(user_id, repo)
    return {
        "user_id": user_id,
        "account_id": item.id,
        "base_currency": item.base_currency,
        "virtual_starting_balance": item.starting_balance,
        "cash": item.current_cash,
        "equity": item.current_equity,
        "realized_pnl": item.realized_pnl,
        "unrealized_pnl": item.unrealized_pnl,
        "fees": item.fees,
        "current_drawdown": item.current_drawdown,
        "max_drawdown": item.max_drawdown,
        "paper_only": True,
    }


@router.get("/{user_id}/positions")
async def positions(user_id: int, repo: Repo) -> dict[str, Any]:
    item = await account(user_id, repo)
    rows = await repo.list_positions(item.id)
    return {"count": len(rows), "positions": [_position(x) for x in rows]}


@router.get("/{user_id}/positions/open")
async def open_positions(user_id: int, repo: Repo) -> dict[str, Any]:
    item = await account(user_id, repo)
    rows = await repo.list_positions(item.id, "OPEN")
    return {"count": len(rows), "positions": [_position(x) for x in rows]}


@router.get("/{user_id}/reports/{report_type}")
async def report(user_id: int, report_type: str, repo: Repo) -> dict[str, Any]:
    if report_type.upper() not in {"DAILY", "WEEKLY", "MONTHLY", "ALL_TIME"}:
        raise HTTPException(status_code=422, detail="invalid report type")
    result = await ReportService(repo).generate(await account(user_id, repo), report_type)
    return result.model_dump(mode="json")


@router.get("/{user_id}/arbitrage/positions")
async def arbitrage_positions(user_id: int, repo: Repo) -> dict[str, Any]:
    item = await account(user_id, repo)
    rows = await repo.list_arbitrage_positions(item.id)
    return {
        "count": len(rows),
        "positions": [
            {
                "id": row.id,
                "opportunity_id": row.opportunity_id,
                "symbol": row.symbol,
                "type": row.arbitrage_type,
                "status": row.status,
                "notional": row.notional,
                "realized_pnl": row.realized_pnl,
                "funding_pnl": row.funding_pnl,
                "paper_only": True,
            }
            for row in rows
        ],
    }


def _position(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "signal_id": item.signal_id,
        "exchange": item.exchange,
        "market": item.market,
        "symbol": item.symbol,
        "direction": item.direction,
        "status": item.status,
        "entry": item.simulated_fill_price,
        "remaining_quantity": item.remaining_quantity,
        "realized_pnl": item.realized_pnl,
        "unrealized_pnl": item.unrealized_pnl,
        "current_stop": item.current_stop,
        "paper_only": True,
    }
