"""Idempotent multi-user paper arbitrage opening and closing."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.arbitrage.paper import paper_notional, two_leg_pnl
from app.config.settings import Settings
from app.db.arbitrage_models import (
    ArbitrageOpportunityRecord,
    PaperArbitrageFillRecord,
    PaperArbitrageLegRecord,
    PaperArbitragePositionRecord,
)
from app.db.portfolio_models import PaperAccountRecord, TelegramUserRecord


class PaperArbitrageService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def open_qualified(self, opportunity: ArbitrageOpportunityRecord, at: datetime) -> int:
        if opportunity.status != "QUALIFIED":
            return 0
        users = list(
            (
                await self.session.execute(
                    select(TelegramUserRecord)
                    .options(selectinload(TelegramUserRecord.preferences))
                    .where(TelegramUserRecord.onboarding_completed.is_(True))
                )
            ).scalars()
        )
        opened = 0
        for user in users:
            pref = user.preferences
            if (
                not pref.arbitrage_notifications_enabled
                or Decimal(str(opportunity.confidence_score)) < pref.minimum_arbitrage_confidence
                or Decimal(str(opportunity.net_edge_pct)) < pref.minimum_arbitrage_net_edge
            ):
                continue
            account = (
                await self.session.execute(
                    select(PaperAccountRecord).where(
                        PaperAccountRecord.user_id == user.id, PaperAccountRecord.active.is_(True)
                    )
                )
            ).scalar_one_or_none()
            if account is None:
                continue
            existing = (
                await self.session.execute(
                    select(PaperArbitragePositionRecord.id).where(
                        PaperArbitragePositionRecord.account_id == account.id,
                        PaperArbitragePositionRecord.opportunity_id == opportunity.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            economics = opportunity.payload["economics"]
            notional = min(
                Decimal(str(opportunity.target_notional)),
                paper_notional(
                    account.current_equity,
                    pref.maximum_paper_arbitrage_allocation,
                    Decimal(str(self.settings.paper_arbitrage_max_allocation_pct)),
                ),
            )
            buy = Decimal(economics["buy_vwap"])
            sell = Decimal(economics["sell_vwap"])
            quantity = min(Decimal(economics["quantity"]), notional / buy)
            fee = notional * Decimal(economics["fees_pct"]) / Decimal("100")
            position = PaperArbitragePositionRecord(
                account_id=account.id,
                opportunity_id=opportunity.id,
                symbol=opportunity.symbol,
                arbitrage_type=opportunity.arbitrage_type,
                notional=notional,
                quantity=quantity,
                opened_at=at,
                status="OPEN",
                fees=fee,
                funding_pnl=Decimal("0"),
                realized_pnl=-fee,
            )
            position.legs = [
                PaperArbitrageLegRecord(
                    side="BUY",
                    exchange=opportunity.buy_exchange,
                    market=opportunity.buy_market,
                    entry_price=buy,
                    quantity=quantity,
                ),
                PaperArbitrageLegRecord(
                    side="SELL",
                    exchange=opportunity.sell_exchange,
                    market=opportunity.sell_market,
                    entry_price=sell,
                    quantity=quantity,
                ),
            ]
            position.fills = [
                PaperArbitrageFillRecord(
                    processing_key=f"{opportunity.id}:OPEN",
                    fill_type="OPEN",
                    occurred_at=at,
                    gross_pnl=Decimal("0"),
                    fees=fee,
                    funding_pnl=Decimal("0"),
                    net_pnl=-fee,
                )
            ]
            self.session.add(position)
            account.current_cash -= fee
            account.realized_pnl -= fee
            account.fees += fee
            opened += 1
        await self.session.commit()
        return opened

    async def close(
        self,
        position: PaperArbitragePositionRecord,
        *,
        buy_exit: Decimal,
        sell_exit: Decimal,
        fees: Decimal,
        funding_pnl: Decimal,
        at: datetime,
        reason: str,
        processing_key: str,
    ) -> bool:
        if position.status != "OPEN" or any(
            fill.processing_key == processing_key for fill in position.fills
        ):
            return False
        buy_leg = next(leg for leg in position.legs if leg.side == "BUY")
        sell_leg = next(leg for leg in position.legs if leg.side == "SELL")
        result = two_leg_pnl(
            buy_entry=Decimal(str(buy_leg.entry_price)),
            buy_exit=buy_exit,
            sell_entry=Decimal(str(sell_leg.entry_price)),
            sell_exit=sell_exit,
            quantity=Decimal(str(position.quantity)),
            fees=fees,
            funding_pnl=funding_pnl,
        )
        buy_leg.exit_price, sell_leg.exit_price = buy_exit, sell_exit
        position.status, position.closed_at, position.exit_reason = "CLOSED", at, reason
        position.fees = Decimal(str(position.fees)) + fees
        position.funding_pnl = funding_pnl
        position.realized_pnl = Decimal(str(position.realized_pnl)) + result.net_pnl
        position.fills.append(
            PaperArbitrageFillRecord(
                processing_key=processing_key,
                fill_type="CLOSE",
                occurred_at=at,
                gross_pnl=result.gross_pnl,
                fees=fees,
                funding_pnl=funding_pnl,
                net_pnl=result.net_pnl,
            )
        )
        account = await self.session.get(PaperAccountRecord, position.account_id)
        if account is not None:
            account.current_cash += result.net_pnl
            account.realized_pnl += result.net_pnl
            account.fees += fees
            account.current_equity = account.current_cash + account.unrealized_pnl
        await self.session.commit()
        return True
