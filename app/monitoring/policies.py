"""Explicit paper/signal stop-management policies."""

from enum import StrEnum


class StopManagementPolicy(StrEnum):
    STATIC = "STATIC"
    BREAKEVEN_AFTER_TP1 = "BREAKEVEN_AFTER_TP1"
    STRUCTURE_TRAILING = "STRUCTURE_TRAILING"


def breakeven_stop(entry: float, bullish: bool, buffer_bps: float) -> float:
    adjustment = entry * buffer_bps / 10_000
    return entry + adjustment if bullish else entry - adjustment


def structure_trailing_stop(
    current_stop: float, confirmed_swing: float, bullish: bool, atr_buffer: float
) -> float | None:
    candidate = confirmed_swing - atr_buffer if bullish else confirmed_swing + atr_buffer
    improves = candidate > current_stop if bullish else candidate < current_stop
    return candidate if improves else None
