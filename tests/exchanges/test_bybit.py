from decimal import Decimal

import pytest

from app.exchanges.bybit.rest import BybitREST
from app.market.models import MarketType, Timeframe


class FakeClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses

    async def get(self, path: str, params: object = None) -> object:
        return self.responses[path]

    async def close(self) -> None:
        return None


def envelope(items: list[object], **extra: object) -> dict[str, object]:
    return {"retCode": 0, "retMsg": "OK", "result": {"list": items, **extra}}


@pytest.mark.asyncio
async def test_bybit_linear_normalizes_reverse_ordered_candles() -> None:
    client = FakeClient(
        {
            "/v5/market/kline": envelope(
                [
                    ["1700000060000", "11", "13", "10", "12", "6", "70"],
                    ["1700000000000", "10", "12", "9", "11", "5", "54"],
                ]
            )
        }
    )
    rest = BybitREST(MarketType.FUTURES, client=client)  # type: ignore[arg-type]

    candles = await rest.get_candles("BTCUSDT", Timeframe.M1, 2)

    assert [candle.close for candle in candles] == [Decimal("11"), Decimal("12")]
    assert candles[0].open_time < candles[1].open_time


@pytest.mark.asyncio
async def test_bybit_linear_normalizes_funding_and_open_interest() -> None:
    client = FakeClient(
        {
            "/v5/market/tickers": envelope(
                [{"fundingRate": "0.0001", "nextFundingTime": "1700000000000"}]
            ),
            "/v5/market/open-interest": envelope(
                [{"openInterest": "123.5", "timestamp": "1700000000000"}]
            ),
        }
    )
    rest = BybitREST(MarketType.FUTURES, client=client)  # type: ignore[arg-type]

    funding = await rest.get_funding_rate("BTCUSDT")
    interest = await rest.get_open_interest("BTCUSDT")

    assert funding is not None and funding.rate == Decimal("0.0001")
    assert interest is not None and interest.value == Decimal("123.5")


@pytest.mark.asyncio
async def test_bybit_symbol_discovery_follows_pagination_cursor() -> None:
    class PagedClient:
        calls = 0

        async def get(self, path: str, params: object = None) -> object:
            assert path == "/v5/market/instruments-info"
            self.calls += 1
            symbol = "BTCUSDT" if self.calls == 1 else "ETHUSDT"
            cursor = "next" if self.calls == 1 else ""
            return envelope(
                [
                    {
                        "symbol": symbol,
                        "baseCoin": symbol.removesuffix("USDT"),
                        "quoteCoin": "USDT",
                        "status": "Trading",
                        "priceFilter": {"tickSize": "0.1"},
                        "lotSizeFilter": {"qtyStep": "0.001"},
                    }
                ],
                nextPageCursor=cursor,
            )

        async def close(self) -> None:
            return None

    client = PagedClient()
    rest = BybitREST(MarketType.FUTURES, client=client)  # type: ignore[arg-type]
    symbols = await rest.get_symbols()
    assert [item.symbol for item in symbols] == ["BTCUSDT", "ETHUSDT"]
    assert client.calls == 2
