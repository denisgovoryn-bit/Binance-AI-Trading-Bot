"""Bybit V5 public REST payload normalization."""

from datetime import UTC, datetime
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

INTERVALS = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
}


class BybitREST:
    def __init__(self, market: MarketType, client: PublicRESTClient | None = None) -> None:
        self.market = market
        self.category = "spot" if market is MarketType.SPOT else "linear"
        self.client = client or PublicRESTClient("https://api.bybit.com")

    async def _result(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = await self.client.get(path, params)
        if payload.get("retCode") != 0:
            raise ExchangeAPIError(f"Bybit {payload.get('retCode')}: {payload.get('retMsg')}")
        result: dict[str, Any] = payload["result"]
        result["_response_time"] = payload.get("time")
        return result

    async def get_symbols(self) -> list[SymbolInfo]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {"category": self.category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            result = await self._result("/v5/market/instruments-info", params)
            items.extend(result["list"])
            next_cursor = result.get("nextPageCursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = str(next_cursor)
        return [
            SymbolInfo(
                exchange=ExchangeName.BYBIT,
                market=self.market,
                symbol=item["symbol"],
                base_asset=item["baseCoin"],
                quote_asset=item["quoteCoin"],
                active=item["status"] == "Trading",
                price_tick=item.get("priceFilter", {}).get("tickSize"),
                quantity_step=item.get("lotSizeFilter", {}).get(
                    "qtyStep", item.get("lotSizeFilter", {}).get("basePrecision")
                ),
                listed_at=utc_from_milliseconds(item["launchTime"])
                if item.get("launchTime")
                else None,
            )
            for item in items
        ]

    async def get_ticker(self, symbol: str) -> Ticker:
        result = await self._result(
            "/v5/market/tickers", {"category": self.category, "symbol": symbol}
        )
        item = result["list"][0]
        return Ticker(
            exchange=ExchangeName.BYBIT,
            market=self.market,
            symbol=item["symbol"],
            last_price=item["lastPrice"],
            bid_price=item["bid1Price"],
            ask_price=item["ask1Price"],
            volume_24h=item["volume24h"],
            quote_volume_24h=item["turnover24h"],
            timestamp=(
                utc_from_milliseconds(result["_response_time"])
                if result.get("_response_time") is not None
                else datetime.now(UTC)
            ),
        )

    async def get_tickers(self) -> list[Ticker]:
        result = await self._result("/v5/market/tickers", {"category": self.category})
        timestamp = (
            utc_from_milliseconds(result["_response_time"])
            if result.get("_response_time") is not None
            else datetime.now(UTC)
        )
        tickers: list[Ticker] = []
        for item in result["list"]:
            if not item.get("bid1Price") or not item.get("ask1Price"):
                continue
            tickers.append(
                Ticker(
                    exchange=ExchangeName.BYBIT,
                    market=self.market,
                    symbol=item["symbol"],
                    last_price=item["lastPrice"],
                    bid_price=item["bid1Price"],
                    ask_price=item["ask1Price"],
                    volume_24h=item["volume24h"],
                    quote_volume_24h=item["turnover24h"],
                    timestamp=timestamp,
                )
            )
        return tickers

    async def get_candles(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        result = await self._result(
            "/v5/market/kline",
            {
                "category": self.category,
                "symbol": symbol,
                "interval": INTERVALS[timeframe],
                "limit": limit,
            },
        )
        candles = [
            Candle(
                exchange=ExchangeName.BYBIT,
                market=self.market,
                symbol=symbol,
                timeframe=timeframe,
                open_time=utc_from_milliseconds(item[0]),
                close_time=utc_from_milliseconds(int(item[0]) + _timeframe_ms(timeframe) - 1),
                open=item[1],
                high=item[2],
                low=item[3],
                close=item[4],
                volume=item[5],
                quote_volume=item[6],
            )
            for item in result["list"]
        ]
        return sorted(candles, key=lambda candle: candle.open_time)

    async def get_orderbook(self, symbol: str, limit: int) -> OrderBook:
        result = await self._result(
            "/v5/market/orderbook", {"category": self.category, "symbol": symbol, "limit": limit}
        )
        return OrderBook(
            exchange=ExchangeName.BYBIT,
            market=self.market,
            symbol=result["s"],
            bids=tuple(OrderBookLevel(price=row[0], quantity=row[1]) for row in result["b"]),
            asks=tuple(OrderBookLevel(price=row[0], quantity=row[1]) for row in result["a"]),
            timestamp=utc_from_milliseconds(result["ts"]),
            sequence=result.get("u"),
        )

    async def get_recent_trades(self, symbol: str, limit: int) -> list[Trade]:
        result = await self._result(
            "/v5/market/recent-trade", {"category": self.category, "symbol": symbol, "limit": limit}
        )
        return [
            Trade(
                exchange=ExchangeName.BYBIT,
                market=self.market,
                symbol=item["s"],
                trade_id=item["i"],
                price=item["p"],
                quantity=item["v"],
                buyer_is_maker=item["S"] == "Sell",
                timestamp=utc_from_milliseconds(item["T"]),
            )
            for item in result["list"]
        ]

    async def get_funding_rate(self, symbol: str) -> FundingRate | None:
        if self.market is MarketType.SPOT:
            return None
        result = await self._result(
            "/v5/market/tickers", {"category": self.category, "symbol": symbol}
        )
        item = result["list"][0]
        return FundingRate(
            exchange=ExchangeName.BYBIT,
            symbol=symbol,
            rate=item["fundingRate"],
            funding_time=utc_from_milliseconds(item["nextFundingTime"]),
        )

    async def get_open_interest(self, symbol: str) -> OpenInterest | None:
        if self.market is MarketType.SPOT:
            return None
        result = await self._result(
            "/v5/market/open-interest",
            {"category": self.category, "symbol": symbol, "intervalTime": "5min", "limit": 1},
        )
        item = result["list"][0]
        return OpenInterest(
            exchange=ExchangeName.BYBIT,
            symbol=symbol,
            value=item["openInterest"],
            timestamp=utc_from_milliseconds(item["timestamp"]),
        )

    async def close(self) -> None:
        await self.client.close()


def _timeframe_ms(timeframe: Timeframe) -> int:
    return {
        Timeframe.M1: 60_000,
        Timeframe.M5: 300_000,
        Timeframe.M15: 900_000,
        Timeframe.H1: 3_600_000,
        Timeframe.H4: 14_400_000,
    }[timeframe]
