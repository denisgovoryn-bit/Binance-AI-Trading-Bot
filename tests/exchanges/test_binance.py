from decimal import Decimal

import pytest

from app.exchanges.binance.rest import BinanceREST
from app.market.models import MarketType, Timeframe


class FakeClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses

    async def get(self, path: str, params: object = None) -> object:
        return self.responses[path]

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_binance_spot_normalizes_ticker_and_candles() -> None:
    client = FakeClient(
        {
            "/api/v3/ticker/24hr": {
                "symbol": "BTCUSDT",
                "lastPrice": "68000",
                "bidPrice": "67999",
                "askPrice": "68001",
                "volume": "100",
                "quoteVolume": "6800000",
                "closeTime": 1_700_000_000_000,
            },
            "/api/v3/klines": [
                [1_700_000_000_000, "10", "12", "9", "11", "5", 1_700_000_059_999, "54"]
            ],
        }
    )
    rest = BinanceREST(MarketType.SPOT, client=client)  # type: ignore[arg-type]

    ticker = await rest.get_ticker("BTCUSDT")
    candles = await rest.get_candles("BTCUSDT", Timeframe.M1, 1)

    assert ticker.spread_bps == Decimal("0.2941176470588235294117647059")
    assert candles[0].close == Decimal("11")
    assert candles[0].timeframe is Timeframe.M1


@pytest.mark.asyncio
async def test_binance_spot_derivative_metrics_are_not_synthetic() -> None:
    rest = BinanceREST(MarketType.SPOT, client=FakeClient({}))  # type: ignore[arg-type]
    assert await rest.get_funding_rate("BTCUSDT") is None
    assert await rest.get_open_interest("BTCUSDT") is None


@pytest.mark.asyncio
async def test_binance_futures_ticker_uses_book_ticker_for_bid_and_ask() -> None:
    client = FakeClient(
        {
            "/fapi/v1/ticker/24hr": {
                "symbol": "BTCUSDT",
                "lastPrice": "68000",
                "volume": "100",
                "quoteVolume": "6800000",
                "closeTime": 1_700_000_000_000,
            },
            "/fapi/v1/ticker/bookTicker": {
                "symbol": "BTCUSDT",
                "bidPrice": "67999",
                "askPrice": "68001",
            },
        }
    )
    rest = BinanceREST(MarketType.FUTURES, client=client)  # type: ignore[arg-type]

    ticker = await rest.get_ticker("BTCUSDT")

    assert ticker.bid_price == Decimal("67999")
    assert ticker.ask_price == Decimal("68001")
