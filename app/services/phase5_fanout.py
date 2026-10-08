"""One event consumer composing paper updates and Telegram delivery."""

from app.db.signal_models import SignalMonitoringEventRecord, SignalRecord
from app.portfolio.paper_trade import PaperTradeService
from app.telegram.notifications import TelegramNotificationService


class Phase5EventFanout:
    def __init__(
        self, paper: PaperTradeService, notifications: TelegramNotificationService
    ) -> None:
        self.paper = paper
        self.notifications = notifications

    async def __call__(self, signal: SignalRecord, event: SignalMonitoringEventRecord) -> None:
        await self.paper.process(signal, event)
        await self.notifications.notify_event(signal, event)
