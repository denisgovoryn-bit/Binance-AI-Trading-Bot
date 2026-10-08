"""Durable, preference-filtered Telegram notification fanout."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from app.db.portfolio_models import UserNotificationDeliveryRecord
from app.db.repositories.portfolio import PortfolioRepository
from app.db.signal_models import SignalMonitoringEventRecord, SignalRecord
from app.telegram.formatting import DISCLAIMER, format_signal, number

SendMessage = Callable[[int, str], Awaitable[int | None]]

MONITORING_NOTIFICATIONS = {
    "ENTRY_APPROACHING",
    "REVALIDATION_FAILED",
    "SETUP_INVALIDATED",
    "SETUP_EXPIRED",
    "ENTRY_CONFIRMED",
    "SIGNAL_ACTIVE",
    "SIGNAL_WEAKENING",
    "SIGNAL_RECOVERED",
    "TP1_REACHED",
    "TP2_REACHED",
    "TP3_REACHED",
    "STOP_UPDATED",
    "STOP_REACHED",
    "SIGNAL_COMPLETED",
    "SIGNAL_CLOSED",
}


class TelegramNotificationService:
    def __init__(self, repository: PortfolioRepository, sender: SendMessage) -> None:
        self.repository = repository
        self.sender = sender

    async def notify_new_signal(self, signal: SignalRecord) -> int:
        if signal.classification not in {"STRONG_SETUP", "VERY_STRONG_SETUP"}:
            return 0
        return await self._fanout(
            signal,
            None,
            f"signal:{signal.id}:{signal.classification}",
            "🟡 <b>SETUP WAITING FOR ENTRY</b>\n\n" + format_signal(signal),
        )

    async def notify_event(self, signal: SignalRecord, event: SignalMonitoringEventRecord) -> int:
        if event.event_type not in MONITORING_NOTIFICATIONS:
            return 0
        return await self._fanout(signal, event, f"event:{event.id}", format_event(signal, event))

    async def _fanout(
        self, signal: SignalRecord, event: SignalMonitoringEventRecord | None, key: str, text: str
    ) -> int:
        sent = 0
        for user in await self.repository.eligible_users():
            pref = user.preferences
            enabled = (
                pref.receive_signal_notifications
                if event is None
                else pref.receive_monitoring_updates
            )
            if (
                not enabled
                or signal.exchange not in pref.enabled_exchanges
                or signal.market not in pref.enabled_markets
                or Decimal(str(signal.current_confidence)) < pref.minimum_confidence
                or (pref.selected_symbols and signal.symbol not in pref.selected_symbols)
            ):
                continue
            if await self.repository.notification_delivery(user.id, key):
                continue
            delivery = UserNotificationDeliveryRecord(
                user_id=user.id,
                event_id=event.id if event else None,
                delivery_key=key,
                status="PENDING",
            )
            await self.repository.add_notification(delivery)
            try:
                message_id = await self.sender(user.telegram_user_id, text)
                delivery.status = "SENT"
                delivery.sent_at = datetime.now(UTC)
                delivery.telegram_message_id = message_id
                sent += 1
            except Exception as exc:
                delivery.status = "FAILED"
                delivery.error = type(exc).__name__
            await self.repository.commit()
        return sent


def format_event(signal: SignalRecord, event: SignalMonitoringEventRecord) -> str:
    headings = {
        "ENTRY_APPROACHING": "🟠 ENTRY APPROACHING",
        "REVALIDATION_FAILED": "❌ SETUP INVALIDATED",
        "SETUP_INVALIDATED": "❌ SETUP INVALIDATED",
        "SETUP_EXPIRED": "⌛ SETUP EXPIRED",
        "ENTRY_CONFIRMED": "✅ ENTRY CONFIRMED — PAPER",
        "SIGNAL_ACTIVE": "🟢 SIGNAL ACTIVE",
        "SIGNAL_WEAKENING": "⚠️ SIGNAL WEAKENING",
        "SIGNAL_RECOVERED": "🔄 SIGNAL RECOVERED",
        "TP1_REACHED": "✅ TP1 REACHED",
        "TP2_REACHED": "✅ TP2 REACHED",
        "TP3_REACHED": "✅ TP3 REACHED",
        "STOP_UPDATED": "🛡 STOP UPDATED",
        "STOP_REACHED": "🔴 STOP REACHED",
        "SIGNAL_COMPLETED": "🏁 SIGNAL COMPLETED",
        "SIGNAL_CLOSED": "⏹ SIGNAL CLOSED",
    }
    lines = [
        f"<b>{headings[event.event_type]}</b>",
        "",
        f"{signal.symbol} {signal.direction}",
        f"{signal.exchange.title()} {signal.market.title()}",
        f"Observed price: {number(event.price)}",
        f"Status: {event.new_status or signal.status}",
    ]
    if event.previous_confidence is not None:
        lines.append(
            f"Confidence: {event.previous_confidence:.1f} → {event.current_confidence:.1f}"
        )
    if event.reasons:
        lines.extend(["", "Reasons:", *[f"• {reason}" for reason in event.reasons[:4]]])
    if event.event_type == "ENTRY_APPROACHING":
        lines.extend(["", "The setup will be revalidated before any paper entry."])
    lines.extend(["", "Trading Execution: 🔒 REAL ORDERS DISABLED", DISCLAIMER])
    return "\n".join(lines)
