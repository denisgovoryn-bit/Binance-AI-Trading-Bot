"""Validated pre-entry lifecycle transitions."""

from app.signals.models import SignalStatus

ALLOWED_TRANSITIONS: dict[SignalStatus, frozenset[SignalStatus]] = {
    SignalStatus.DISCOVERED: frozenset(
        {SignalStatus.WATCHLIST, SignalStatus.WAITING_ENTRY, SignalStatus.REJECTED}
    ),
    SignalStatus.WATCHLIST: frozenset(
        {SignalStatus.WAITING_ENTRY, SignalStatus.INVALIDATED, SignalStatus.EXPIRED}
    ),
    SignalStatus.WAITING_ENTRY: frozenset(
        {
            SignalStatus.REVALIDATING,
            SignalStatus.INVALIDATED,
            SignalStatus.EXPIRED,
            SignalStatus.CLOSED,
        }
    ),
    SignalStatus.REVALIDATING: frozenset(
        {
            SignalStatus.WAITING_ENTRY,
            SignalStatus.ENTRY_CONFIRMED,
            SignalStatus.INVALIDATED,
            SignalStatus.EXPIRED,
        }
    ),
    SignalStatus.ENTRY_CONFIRMED: frozenset({SignalStatus.ACTIVE, SignalStatus.CLOSED}),
    SignalStatus.ACTIVE: frozenset(
        {
            SignalStatus.WEAKENING,
            SignalStatus.TP1_REACHED,
            SignalStatus.TP2_REACHED,
            SignalStatus.TP3_REACHED,
            SignalStatus.STOPPED,
            SignalStatus.COMPLETED,
            SignalStatus.CLOSED,
        }
    ),
    SignalStatus.WEAKENING: frozenset(
        {
            SignalStatus.ACTIVE,
            SignalStatus.TP1_REACHED,
            SignalStatus.TP2_REACHED,
            SignalStatus.TP3_REACHED,
            SignalStatus.STOPPED,
            SignalStatus.COMPLETED,
            SignalStatus.CLOSED,
        }
    ),
    SignalStatus.TP1_REACHED: frozenset(
        {
            SignalStatus.WEAKENING,
            SignalStatus.TP2_REACHED,
            SignalStatus.TP3_REACHED,
            SignalStatus.STOPPED,
            SignalStatus.COMPLETED,
            SignalStatus.CLOSED,
        }
    ),
    SignalStatus.TP2_REACHED: frozenset(
        {
            SignalStatus.WEAKENING,
            SignalStatus.TP3_REACHED,
            SignalStatus.STOPPED,
            SignalStatus.COMPLETED,
            SignalStatus.CLOSED,
        }
    ),
    SignalStatus.TP3_REACHED: frozenset({SignalStatus.COMPLETED, SignalStatus.CLOSED}),
    SignalStatus.STOPPED: frozenset({SignalStatus.CLOSED}),
    SignalStatus.COMPLETED: frozenset({SignalStatus.CLOSED}),
    SignalStatus.CLOSED: frozenset(),
    SignalStatus.REJECTED: frozenset(),
    SignalStatus.INVALIDATED: frozenset(),
    SignalStatus.EXPIRED: frozenset(),
}


class InvalidSignalTransition(ValueError):
    pass


def validate_transition(previous: SignalStatus, new: SignalStatus) -> None:
    if new not in ALLOWED_TRANSITIONS[previous]:
        raise InvalidSignalTransition(f"transition {previous.value} -> {new.value} is not allowed")
