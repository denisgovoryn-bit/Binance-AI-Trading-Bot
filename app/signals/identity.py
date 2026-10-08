"""Stable structural signal and display-group identities."""

import hashlib
import json
from datetime import datetime

from app.signals.models import DirectionalSignal


def signal_fingerprint(signal: DirectionalSignal) -> str:
    """Hash venue identity plus structural origin; confidence is deliberately excluded."""
    entry = signal.entry_zone
    raw = {
        "exchange": signal.exchange.value,
        "market": signal.market_type.value,
        "symbol": signal.symbol,
        "direction": signal.direction.value if signal.direction else "NONE",
        "timeframe": signal.primary_timeframe.value,
        "setup_type": signal.signal_type.value,
        "entry_type": entry.entry_type.value if entry else "NONE",
        "entry": [round(entry.low, 8), round(entry.high, 8)] if entry else None,
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def correlation_group_key(signal: DirectionalSignal, at: datetime | None = None) -> str:
    timestamp = at or signal.analysis_timestamp
    bucket = int(timestamp.timestamp()) // 3600
    direction = signal.direction.value if signal.direction else "NONE"
    return (
        f"{signal.symbol}:{direction}:{signal.market_type.value}:"
        f"{signal.primary_timeframe.value}:{bucket}"
    )
