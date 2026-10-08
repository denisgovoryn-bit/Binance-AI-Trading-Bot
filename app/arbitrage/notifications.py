"""Preference-filtered durable Telegram arbitrage notifications."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from app.db.arbitrage_models import ArbitrageOpportunityRecord
from app.db.portfolio_models import UserNotificationDeliveryRecord
from app.db.repositories.portfolio import PortfolioRepository
from app.telegram.formatting import format_arbitrage

SendMessage = Callable[[int, str], Awaitable[int | None]]


class ArbitrageNotificationService:
    def __init__(self, repository: PortfolioRepository, sender: SendMessage) -> None:
        self.repository = repository
        self.sender = sender

    async def notify(self, opportunity: ArbitrageOpportunityRecord, event_type: str) -> int:
        if event_type not in {
            "QUALIFIED",
            "WEAKENING",
            "INVALIDATED",
            "PAPER_OPENED",
            "PAPER_CLOSED",
        }:
            return 0
        key = f"arbitrage:{opportunity.id}:{event_type}"
        sent = 0
        for user in await self.repository.eligible_users():
            pref = user.preferences
            if (
                not pref.arbitrage_notifications_enabled
                or Decimal(str(opportunity.confidence_score)) < pref.minimum_arbitrage_confidence
                or Decimal(str(opportunity.net_edge_pct)) < pref.minimum_arbitrage_net_edge
            ):
                continue
            if await self.repository.notification_delivery(user.id, key):
                continue
            delivery = UserNotificationDeliveryRecord(
                user_id=user.id, event_id=None, delivery_key=key, status="PENDING"
            )
            await self.repository.add_notification(delivery)
            try:
                delivery.telegram_message_id = await self.sender(
                    user.telegram_user_id, format_arbitrage(opportunity)
                )
                delivery.status, delivery.sent_at = "SENT", datetime.now(UTC)
                sent += 1
            except Exception as exc:
                delivery.status, delivery.error = "FAILED", type(exc).__name__
            await self.repository.commit()
        return sent
