"""Timezone-aware selection for scheduled paper reports."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db.portfolio_models import TelegramUserRecord


def report_due(user: TelegramUserRecord, cadence: str, now: datetime) -> bool:
    try:
        local = now.astimezone(ZoneInfo(user.timezone))
    except ZoneInfoNotFoundError:
        local = now.astimezone(ZoneInfo("UTC"))
    preferences = user.preferences
    cadence = cadence.upper()
    if local.hour != 9 or local.minute != 0:
        return False
    if cadence == "DAILY":
        return preferences.receive_daily_reports
    if cadence == "WEEKLY":
        return preferences.receive_weekly_reports and local.weekday() == 0
    if cadence == "MONTHLY":
        return preferences.receive_monthly_reports and local.day == 1
    return False
