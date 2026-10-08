"""Executable cross-venue economics, independent of directional strategy."""

from decimal import Decimal

from app.arbitrage.fees import ArbitrageFeeProvider
from app.arbitrage.models import ArbitrageEconomics, BookSnapshot
from app.arbitrage.vwap import buy_vwap, sell_vwap
from app.config.settings import Settings


def calculate_cross_book(
    buy: BookSnapshot,
    sell: BookSnapshot,
    notional: Decimal,
    fees: ArbitrageFeeProvider,
    settings: Settings,
) -> ArbitrageEconomics:
    bought = buy_vwap(buy.asks, notional)
    sold = sell_vwap(sell.bids, bought.quantity, notional)
    executable = min(bought.executable_notional, sold.executable_notional)
    gross = (sell.bids[0].price - buy.asks[0].price) / buy.asks[0].price * Decimal("100")
    fee_pct = fees.taker_fee_pct(buy.exchange.value, buy.market.value) + fees.taker_fee_pct(
        sell.exchange.value, sell.market.value
    )
    slippage = bought.slippage_pct + sold.slippage_pct
    buffer_pct = Decimal(str(settings.arbitrage_safety_buffer_bps)) / Decimal("100")
    net = gross - fee_pct - slippage - buffer_pct
    return ArbitrageEconomics(
        target_notional=notional,
        executable_notional=executable,
        gross_spread_pct=gross,
        fees_pct=fee_pct,
        slippage_pct=slippage,
        safety_buffer_pct=buffer_pct,
        expected_net_edge_pct=net,
        expected_net_pnl=notional * net / Decimal("100"),
        buy_vwap=bought.vwap,
        sell_vwap=sold.vwap,
        quantity=bought.quantity,
    )
