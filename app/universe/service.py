"""Cached, failure-isolated public symbol discovery and deterministic ranking."""

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from app.config.settings import Settings
from app.exchanges.base import BaseExchange
from app.market.models import ExchangeName, MarketType, SymbolInfo, Ticker
from app.universe.models import UniverseEntry, UniverseSnapshot

logger = logging.getLogger(__name__)

CORE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
STABLE_ASSETS = {"USDC", "USDE", "FDUSD", "TUSD", "DAI", "USDP", "BUSD"}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


class SymbolUniverseService:
    def __init__(
        self,
        settings: Settings,
        exchanges: dict[tuple[ExchangeName, MarketType], BaseExchange],
    ) -> None:
        self.settings = settings
        self.exchanges = exchanges
        self.snapshot: UniverseSnapshot | None = None
        self._lock = asyncio.Lock()

    async def refresh(self) -> UniverseSnapshot:
        async with self._lock:
            now = datetime.now(UTC)
            results = await asyncio.gather(
                *(
                    asyncio.wait_for(self._discover(key, adapter, now), timeout=30)
                    for key, adapter in self.exchanges.items()
                ),
                return_exceptions=True,
            )
            discovered: list[UniverseEntry] = []
            degraded: list[str] = []
            used_fallback = False
            for key, result in zip(self.exchanges, results, strict=True):
                label = f"{key[0].value}_{key[1].value}"
                if isinstance(result, BaseException):
                    degraded.append(label)
                    logger.warning("Universe discovery degraded for %s", label)
                else:
                    rows, fallback = result
                    discovered.extend(rows)
                    if fallback:
                        degraded.append(label)
                        used_fallback = True
            if not discovered and self.snapshot is not None:
                return self.snapshot
            availability: dict[tuple[MarketType, str], int] = {}
            for row in discovered:
                availability_key = (row.market, row.symbol)
                availability[availability_key] = availability.get(availability_key, 0) + 1
            selected: list[UniverseEntry] = []
            for exchange, market in self.exchanges:
                rows = [
                    row.model_copy(
                        update={"cross_venue_count": availability[(row.market, row.symbol)]}
                    )
                    for row in discovered
                    if row.exchange is exchange and row.market is market
                ]
                rows.sort(key=_rank_key)
                selected.extend(rows[: self.settings.symbol_universe_max_per_market])
            snapshot = UniverseSnapshot(
                entries=tuple(selected),
                refreshed_at=now,
                degraded_markets=tuple(degraded),
                used_fallback=used_fallback,
            )
            if snapshot.entries or self.snapshot is None:
                self.snapshot = snapshot
            return self.snapshot

    async def _discover(
        self,
        key: tuple[ExchangeName, MarketType],
        adapter: BaseExchange,
        now: datetime,
    ) -> tuple[list[UniverseEntry], bool]:
        symbols = await adapter.get_symbols()
        try:
            tickers = await adapter.get_tickers()
        except Exception:
            return self._fallback(symbols, now), True
        ticker_map = {ticker.symbol.upper(): ticker for ticker in tickers}
        return (
            [
                entry
                for info in symbols
                if (entry := self._normalize(info, ticker_map.get(info.symbol.upper()), now))
                is not None
            ],
            False,
        )

    def _fallback(self, symbols: list[SymbolInfo], now: datetime) -> list[UniverseEntry]:
        blacklist = set(self.settings.symbol_blacklist) | set(
            self.settings.symbol_universe_blacklist
        )
        supported = {info.symbol.upper(): info for info in symbols}
        result: list[UniverseEntry] = []
        for symbol in CORE_SYMBOLS:
            info = supported.get(symbol)
            if (
                info is None
                or not info.active
                or info.quote_asset.upper() != self.settings.symbol_universe_quote_asset
                or symbol in blacklist
            ):
                continue
            result.append(
                UniverseEntry(
                    symbol=symbol,
                    exchange=info.exchange,
                    market=info.market,
                    base_asset=info.base_asset.upper(),
                    quote_asset=info.quote_asset.upper(),
                    active=True,
                    quote_volume_24h=0,
                    spread_pct=0,
                    listed_at=info.listed_at,
                    listing_age_days=(now - info.listed_at).days if info.listed_at else None,
                    refreshed_at=now,
                )
            )
        return result

    def _normalize(
        self, info: SymbolInfo, ticker: Ticker | None, now: datetime
    ) -> UniverseEntry | None:
        settings = self.settings
        symbol = info.symbol.upper()
        blacklist = set(settings.symbol_blacklist) | set(settings.symbol_universe_blacklist)
        whitelist = set(settings.symbol_whitelist) | set(settings.symbol_universe_whitelist)
        if (
            not info.active
            or info.quote_asset.upper() != settings.symbol_universe_quote_asset
            or symbol in blacklist
            or (whitelist and symbol not in whitelist)
            or _is_leveraged_token(info.base_asset)
            or (
                not settings.symbol_universe_include_stable_pairs
                and info.base_asset.upper() in STABLE_ASSETS
            )
            or ticker is None
            or ticker.quote_volume_24h < Decimal(str(settings.symbol_universe_min_24h_quote_volume))
            or ticker.spread_bps / Decimal("100")
            > Decimal(str(settings.symbol_universe_max_spread_pct))
        ):
            return None
        age: int | None = None
        if info.listed_at is not None:
            age = max(0, (now - info.listed_at).days)
            if age < settings.symbol_universe_min_listing_age_days:
                return None
        return UniverseEntry(
            symbol=symbol,
            exchange=info.exchange,
            market=info.market,
            base_asset=info.base_asset.upper(),
            quote_asset=info.quote_asset.upper(),
            active=True,
            quote_volume_24h=ticker.quote_volume_24h,
            spread_pct=ticker.spread_bps / Decimal("100"),
            listed_at=info.listed_at,
            listing_age_days=age,
            refreshed_at=now,
        )


def _rank_key(row: UniverseEntry) -> tuple[int, int, Decimal, Decimal, Decimal, str]:
    core_rank = CORE_SYMBOLS.index(row.symbol) if row.symbol in CORE_SYMBOLS else len(CORE_SYMBOLS)
    return (
        0 if row.symbol in CORE_SYMBOLS else 1,
        core_rank,
        -Decimal(row.cross_venue_count),
        -row.quote_volume_24h,
        row.spread_pct,
        row.symbol,
    )


def _is_leveraged_token(base_asset: str) -> bool:
    base = base_asset.upper()
    return any(
        base.endswith(suffix) and len(base) >= len(suffix) + 2 for suffix in LEVERAGED_SUFFIXES
    )
