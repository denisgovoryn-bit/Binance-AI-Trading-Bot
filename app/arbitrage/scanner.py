"""Low-latency in-memory cross-exchange arbitrage scanner."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.arbitrage.engine import calculate_cross_book
from app.arbitrage.fees import ArbitrageFeeProvider
from app.arbitrage.models import ArbitrageEconomics, BookSnapshot
from app.config.settings import Settings
from app.market.models import ExchangeName, MarketType


@dataclass(frozen=True)
class ArbitrageCandidate:
    symbol: str
    market: MarketType
    buy_exchange: ExchangeName
    sell_exchange: ExchangeName
    economics: ArbitrageEconomics
    buy_age_ms: Decimal
    sell_age_ms: Decimal
    timestamp_skew_ms: Decimal


@dataclass
class ScanMetrics:
    updates: int = 0
    evaluations: int = 0
    gross_candidates: int = 0
    qualified: int = 0
    stale_rejections: int = 0
    skew_rejections: int = 0
    liquidity_rejections: int = 0
    slippage_rejections: int = 0
    fee_edge_rejections: int = 0
    malformed_rejections: int = 0
    errors: int = 0


@dataclass
class ArbitrageScanner:
    settings: Settings
    books: dict[tuple[ExchangeName, MarketType, str], BookSnapshot] = field(default_factory=dict)
    metrics: ScanMetrics = field(default_factory=ScanMetrics)

    def __post_init__(self) -> None:
        self._fees = ArbitrageFeeProvider(self.settings)

    def update(
        self, book: BookSnapshot, *, now: datetime | None = None
    ) -> list[ArbitrageCandidate]:
        self.metrics.updates += 1
        key = (book.exchange, book.market, book.symbol.upper())
        self.books[key] = book
        other = (
            ExchangeName.BYBIT if book.exchange is ExchangeName.BINANCE else ExchangeName.BINANCE
        )
        peer = self.books.get((other, book.market, book.symbol.upper()))
        if peer is None:
            return []
        current = now or datetime.now(UTC)
        if not book.recovery_complete or not peer.recovery_complete:
            self.metrics.stale_rejections += 1
            return []
        ages = (self._age(book, current), self._age(peer, current))
        if max(ages) > Decimal(self.settings.arbitrage_max_quote_age_ms):
            self.metrics.stale_rejections += 1
            return []
        skew = abs(
            Decimal(str((book.exchange_timestamp - peer.exchange_timestamp).total_seconds() * 1000))
        )
        if skew > Decimal(self.settings.arbitrage_max_cross_exchange_skew_ms):
            self.metrics.skew_rejections += 1
            return []
        candidates: list[ArbitrageCandidate] = []
        for buy, sell in ((book, peer), (peer, book)):
            for raw_notional in self.settings.arbitrage_target_notionals:
                self.metrics.evaluations += 1
                try:
                    economics = calculate_cross_book(
                        buy, sell, Decimal(str(raw_notional)), self._fees, self.settings
                    )
                except ValueError:
                    self.metrics.liquidity_rejections += 1
                    continue
                if economics.gross_spread_pct <= 0:
                    continue
                self.metrics.gross_candidates += 1
                if economics.slippage_pct * 100 > Decimal(
                    str(self.settings.arbitrage_max_slippage_bps)
                ):
                    self.metrics.slippage_rejections += 1
                    continue
                if economics.expected_net_edge_pct * 100 < Decimal(
                    str(self.settings.arbitrage_min_net_edge_bps)
                ):
                    self.metrics.fee_edge_rejections += 1
                    continue
                candidates.append(
                    ArbitrageCandidate(
                        symbol=book.symbol,
                        market=book.market,
                        buy_exchange=buy.exchange,
                        sell_exchange=sell.exchange,
                        economics=economics,
                        buy_age_ms=self._age(buy, current),
                        sell_age_ms=self._age(sell, current),
                        timestamp_skew_ms=skew,
                    )
                )
                self.metrics.qualified += 1
        return candidates

    @staticmethod
    def _age(book: BookSnapshot, now: datetime) -> Decimal:
        return max(
            Decimal("0"), Decimal(str((now - book.exchange_timestamp).total_seconds() * 1000))
        )
