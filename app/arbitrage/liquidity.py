"""Conversion and validation for normalized exchange order books."""

from datetime import UTC, datetime

from app.arbitrage.models import BookLevel, BookSnapshot
from app.market.models import OrderBook


def normalize_book(
    book: OrderBook, *, received_at: datetime | None = None, recovery_complete: bool = True
) -> BookSnapshot:
    """Convert a public adapter book and retain exchange/receive timing separately."""
    received = received_at or datetime.now(UTC)
    return BookSnapshot(
        exchange=book.exchange,
        market=book.market,
        symbol=book.symbol.upper(),
        bids=tuple(BookLevel(price=level.price, quantity=level.quantity) for level in book.bids),
        asks=tuple(BookLevel(price=level.price, quantity=level.quantity) for level in book.asks),
        exchange_timestamp=book.timestamp,
        received_at=received,
        sequence=book.sequence,
        recovery_complete=recovery_complete,
    )
