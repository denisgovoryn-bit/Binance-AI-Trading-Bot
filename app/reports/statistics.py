"""Deterministic user/global paper statistics."""

from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal

from app.db.portfolio_models import PaperAccountRecord, PaperPositionRecord
from app.reports.models import PerformanceMetrics


def confidence_bracket(value: Decimal) -> str:
    if value < 80:
        return "70-79"
    if value < 85:
        return "80-84"
    if value < 90:
        return "85-89"
    if value < 95:
        return "90-94"
    return "95-100"


def asset_group(symbol: str) -> str:
    if symbol.startswith("BTC"):
        return "BTC"
    if symbol.startswith("ETH"):
        return "ETH"
    return "ALTCOINS"


def calculate_metrics(
    account: PaperAccountRecord | None,
    positions: list[PaperPositionRecord],
    starting_equity: Decimal | None = None,
    ending_equity: Decimal | None = None,
) -> PerformanceMetrics:
    closed = [x for x in positions if x.status != "OPEN"]
    opened = [x for x in positions if x.status == "OPEN"]
    results = [x.realized_pnl for x in closed]
    wins = [x for x in results if x > 0]
    losses = [x for x in results if x < 0]
    zero = [x for x in results if x == 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = sum(losses, Decimal("0"))
    start = starting_equity or (account.starting_balance if account else Decimal("0"))
    end = ending_equity or (
        account.current_equity if account else start + sum(results, Decimal("0"))
    )
    net = end - start
    durations = [
        Decimal(str((x.closed_at - x.opened_at).total_seconds()))
        for x in closed
        if x.closed_at is not None
    ]
    segments: dict[str, dict[str, dict[str, Decimal | int | None]]] = {}
    dimensions: dict[str, Callable[[PaperPositionRecord], str]] = {
        "exchange": lambda x: x.exchange,
        "market": lambda x: x.market,
        "direction": lambda x: x.direction,
        "asset": lambda x: asset_group(x.symbol),
        "confidence": lambda x: confidence_bracket(x.confidence),
        "timeframe": lambda x: x.timeframe,
        "setup_type": lambda x: x.setup_type,
    }
    for name, key_fn in dimensions.items():
        grouped: dict[str, list[PaperPositionRecord]] = defaultdict(list)
        for position in closed:
            grouped[key_fn(position)].append(position)
        segments[name] = {key: _segment(rows) for key, rows in grouped.items()}
    return PerformanceMetrics(
        starting_equity=start,
        ending_equity=end,
        net_pnl=net,
        return_pct=_pct(net, start),
        fees=account.fees if account else sum((x.fees for x in positions), Decimal("0")),
        open_positions=len(opened),
        closed_trades=len(closed),
        winning_trades=len(wins),
        losing_trades=len(losses),
        breakeven_trades=len(zero),
        win_rate=_pct(Decimal(len(wins)), Decimal(len(closed))),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=(gross_profit / abs(gross_loss)).quantize(Decimal("0.0001"))
        if gross_loss
        else None,
        average_winner=_average(wins),
        average_loser=_average(losses),
        expectancy=_average(results),
        average_r=_average_r(closed),
        max_drawdown=account.max_drawdown if account else Decimal("0"),
        current_drawdown=account.current_drawdown if account else Decimal("0"),
        best_trade=max(results) if results else None,
        worst_trade=min(results) if results else None,
        average_holding_seconds=_average(durations),
        segments=segments,
    )


def _segment(rows: list[PaperPositionRecord]) -> dict[str, Decimal | int | None]:
    pnl = [x.realized_pnl for x in rows]
    wins = sum(x > 0 for x in pnl)
    profit = sum((x for x in pnl if x > 0), Decimal("0"))
    loss = sum((x for x in pnl if x < 0), Decimal("0"))
    return {
        "count": len(rows),
        "net_pnl": sum(pnl, Decimal("0")),
        "win_rate": _pct(Decimal(wins), Decimal(len(rows))),
        "profit_factor": (profit / abs(loss)).quantize(Decimal("0.0001")) if loss else None,
    }


def _average(values: list[Decimal]) -> Decimal:
    return (
        Decimal("0")
        if not values
        else (sum(values, Decimal("0")) / len(values)).quantize(Decimal("0.0001"))
    )


def _average_r(positions: list[PaperPositionRecord]) -> Decimal:
    values = []
    for position in positions:
        risk = (
            abs(position.simulated_fill_price - position.initial_stop) * position.initial_quantity
        )
        if risk:
            values.append(position.realized_pnl / risk)
    return _average(values)


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (
        Decimal("0")
        if denominator == 0
        else (numerator / denominator * 100).quantize(Decimal("0.01"))
    )
