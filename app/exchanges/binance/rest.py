"""Binance public REST payload normalization."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.exchanges.http import ExchangeAPIError, PublicRESTClient
from app.market.models import (
    Candle,
    ExchangeName,
    FundingRate,
    MarketType,
    OpenInterest,
    OrderBook,
    OrderBookLevel,
    SymbolInfo,
    Ticker,
    Timeframe,
    Trade,
    utc_from_milliseconds,
)


class BinanceREST:
    def __init__(self, market: MarketType, client: PublicRESTClient | None = None) -> None:
        self.market = market
        base_url = (
            "https://api.binance.com" if market is MarketType.SPOT else "https://fapi.binance.com"
        )
        self.client = client or PublicRESTClient(base_url)
        self.prefix = "/api/v3" if market is MarketType.SPOT else "/fapi/v1"

    async def get_symbols(self) -> list[SymbolInfo]:
        payload = await self.client.get(f"{self.prefix}/exchangeInfo")
        result: list[SymbolInfo] = []
        for item in payload["symbols"]:
            filters = {entry["filterType"]: entry for entry in item.get("filters", [])}
            price_filter = filters.get("PRICE_FILTER", {})
            lot_filter = filters.get("LOT_SIZE", {})
            active_status = "TRADING"
            result.append(
                SymbolInfo(
                    exchange=ExchangeName.BINANCE,
                    market=self.market,
                    symbol=item["symbol"],
                    base_asset=item["baseAsset"],
                    quote_asset=item["quoteAsset"],
                    active=item["status"] == active_status,
                    price_tick=_decimal_or_none(price_filter.get("tickSize")),
                    quantity_step=_decimal_or_none(lot_filter.get("stepSize")),
                )
            )
        return result

    async def get_ticker(self, symbol: str) -> Ticker:
        payload = await self.client.get(f"{self.prefix}/ticker/24hr", {"symbol": symbol})
        book = payload
        if "bidPrice" not in payload:
            book = await self.client.get(f"{self.prefix}/ticker/bookTicker", {"symbol": symbol})
        return Ticker(
            exchange=ExchangeName.BINANCE,
            market=self.market,
            symbol=payload["symbol"],
            last_price=payload["lastPrice"],
            bid_price=book["bidPrice"],
            ask_price=book["askPrice"],
            volume_24h=payload["volume"],
            quote_volume_24h=payload["quoteVolume"],
            timestamp=utc_from_milliseconds(payload["closeTime"]),
        )

    async def get_tickers(self) -> list[Ticker]:
        payload = await self.client.get(f"{self.prefix}/ticker/24hr")
        rows = payload if isinstance(payload, list) else []
        books: dict[str, dict[str, Any]] = {}
        if self.market is MarketType.FUTURES:
            raw_books = await self.client.get(f"{self.prefix}/ticker/bookTicker")
            if isinstance(raw_books, list):
                books = {str(row["symbol"]): row for row in raw_books}
        result: list[Ticker] = []
        for row in rows:
            book = row if "bidPrice" in row else books.get(str(row.get("symbol")), {})
            if not book.get("bidPrice") or not book.get("askPrice"):
                continue
            result.append(
                Ticker(
                    exchange=ExchangeName.BINANCE,
                    market=self.market,
                    symbol=row["symbol"],
                    last_price=row["lastPrice"],
                    bid_price=book["bidPrice"],
                    ask_price=book["askPrice"],
                    volume_24h=row["volume"],
                    quote_volume_24h=row["quoteVolume"],
                    timestamp=utc_from_milliseconds(row["closeTime"]),
                )
            )
        return result

    async def get_candles(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        payload = await self.client.get(
            f"{self.prefix}/klines", {"symbol": symbol, "interval": timeframe.value, "limit": limit}
        )
        return [
            Candle(
                exchange=ExchangeName.BINANCE,
                market=self.market,
                symbol=symbol,
                timeframe=timeframe,
                open_time=utc_from_milliseconds(item[0]),
                close_time=utc_from_milliseconds(item[6]),
                open=item[1],
                high=item[2],
                low=item[3],
                close=item[4],
                volume=item[5],
                quote_volume=item[7],
            )
            for item in payload
        ]

    async def get_orderbook(self, symbol: str, limit: int) -> OrderBook:
        payload = await self.client.get(f"{self.prefix}/depth", {"symbol": symbol, "limit": limit})
        return OrderBook(
            exchange=ExchangeName.BINANCE,
            market=self.market,
            symbol=symbol,
            bids=tuple(OrderBookLevel(price=row[0], quantity=row[1]) for row in payload["bids"]),
            asks=tuple(OrderBookLevel(price=row[0], quantity=row[1]) for row in payload["asks"]),
            timestamp=(
                utc_from_milliseconds(payload.get("E", payload.get("T")))
                if payload.get("E", payload.get("T")) is not None
                else datetime.now(UTC)
            ),
            sequence=payload.get("lastUpdateId", payload.get("u")),
        )

    async def get_recent_trades(self, symbol: str, limit: int) -> list[Trade]:
        payload = await self.client.get(f"{self.prefix}/trades", {"symbol": symbol, "limit": limit})
        return [
            Trade(
                exchange=ExchangeName.BINANCE,
                market=self.market,
                symbol=symbol,
                trade_id=str(item["id"]),
                price=item["price"],
                quantity=item["qty"],
                buyer_is_maker=item["isBuyerMaker"],
                timestamp=utc_from_milliseconds(item["time"]),
            )
            for item in payload
        ]

    async def get_funding_rate(self, symbol: str) -> FundingRate | None:
        if self.market is MarketType.SPOT:
            return None
        payload = await self.client.get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return FundingRate(
            exchange=ExchangeName.BINANCE,
            symbol=symbol,
            rate=payload["lastFundingRate"],
            funding_time=utc_from_milliseconds(payload["nextFundingTime"]),
        )

    async def get_open_interest(self, symbol: str) -> OpenInterest | None:
        if self.market is MarketType.SPOT:
            return None
        payload = await self.client.get("/fapi/v1/openInterest", {"symbol": symbol})
        return OpenInterest(
            exchange=ExchangeName.BINANCE,
            symbol=symbol,
            value=payload["openInterest"],
            timestamp=utc_from_milliseconds(payload["time"]),
        )

    async def close(self) -> None:
        await self.client.close()


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError as exc:
        raise ExchangeAPIError(f"invalid decimal value: {value}") from exc
