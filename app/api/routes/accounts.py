"""Safe, read-only real account API views."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/accounts", tags=["real accounts — read only"])


@router.get("")
async def accounts(request: Request) -> dict[str, object]:
    rows = await request.app.state.account_service.get_accounts()
    return {
        "mode": "REAL ACCOUNT — READ ONLY",
        "accounts": [row.model_dump(mode="json") for row in rows],
        "trading_execution": "DISABLED",
    }


@router.get("/{exchange}")
async def account(exchange: str, request: Request) -> dict[str, object]:
    row = await request.app.state.account_service.get_account(exchange)
    return {
        "mode": "REAL ACCOUNT — READ ONLY",
        "account": row.model_dump(mode="json"),
        "trading_execution": "DISABLED",
    }
