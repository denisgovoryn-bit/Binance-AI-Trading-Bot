"""Telegram presentation only; no signal or accounting decisions."""

from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from typing import Any

from app.db.arbitrage_models import ArbitrageOpportunityRecord, PaperArbitragePositionRecord
from app.db.portfolio_models import PaperAccountRecord, PaperPositionRecord
from app.db.signal_models import SignalRecord
from app.reports.models import PerformanceReport

DISCLAIMER = (
    "Confidence is strategy-condition agreement, not win probability. No real order was placed."
)


def welcome_text(first_name: str | None = None) -> str:
    name = f", {escape(first_name)}" if first_name else ""
    return (
        f"Welcome{name}.\n\nThis bot provides market signals and virtual paper trading only. "
        "It cannot place real orders or access exchange funds.\n\n"
        "Enter your starting <b>Virtual / Paper Balance</b> in USDT, for example: <code>1000</code>"
    )


def onboarding_summary(account: PaperAccountRecord, markets: list[str]) -> str:
    return (
        "✅ Paper account ready\n\n"
        f"Virtual balance: <b>{money(account.starting_balance)} USDT</b>\n"
        f"Markets: {', '.join(markets)}\nProfile: BALANCED paper simulation\n\n"
        "No real funds were deposited."
    )


def format_signal(record: SignalRecord, rank: int | None = None) -> str:
    payload = record.payload
    prefix = f"#{rank} " if rank else ""
    entry = payload.get("entry_zone") or {}
    invalidation = payload.get("invalidation") or {}
    targets = payload.get("targets") or []
    lines = [
        f"{prefix}<b>{escape(record.symbol)}</b>",
        f"{record.exchange.title()} {record.market.title()} · {record.direction}",
        f"Confidence: <b>{record.current_confidence:.1f}/100</b>",
        f"Entry: {number(entry.get('low'))}–{number(entry.get('high'))}",
        f"Stop: {number(invalidation.get('price'))}",
    ]
    for target in targets:
        lines.append(f"{target['name']}: {number(target['price'])} ({target['r_multiple']:.2f}R)")
    lines.append(f"Status: {record.status.replace('_', ' ')}")
    reasons = payload.get("reasons") or []
    if reasons:
        lines.append("Reason: " + escape("; ".join(reasons[:3])))
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("Warnings: " + escape("; ".join(warnings[:2])))
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def format_pending(record: SignalRecord, rank: int | None = None) -> str:
    payload = record.payload
    entry = payload.get("entry_zone") or {}
    risk = payload.get("risk_reward") or {}
    metrics = payload.get("supporting_metrics") or {}
    current = record.current_monitored_price or metrics.get("current_price")
    bid = record.current_bid_price
    ask = record.current_ask_price
    bullish = record.direction in {"BUY", "LONG"}
    executable = ask if bullish else bid
    if executable is None:
        executable = record.current_executable_price
    distance_price = executable if executable is not None else current
    atr = metrics.get("atr")
    distance = None
    if (
        distance_price is not None
        and atr
        and record.entry_low is not None
        and record.entry_high is not None
    ):
        raw = (
            record.entry_low - distance_price
            if distance_price < record.entry_low
            else distance_price - record.entry_high
            if distance_price > record.entry_high
            else 0
        )
        distance = raw / atr
    prefix = f"#{rank} " if rank else ""
    targets = record.targets
    lines = [
        f"{prefix}<b>{escape(record.symbol)}</b> · {record.market.title()} {record.direction}",
        f"Confidence: <b>{record.current_confidence:.1f}/100</b>",
        "Current:",
        f"Last {number(current)}",
        f"Bid {number(bid)}",
        f"Ask {number(ask)}",
        f"Executable quote: {number(executable)} {'ASK' if bullish else 'BID'}",
        f"Entry zone: {number(record.entry_low)}–{number(record.entry_high)}",
        f"Distance to entry: {distance:.2f} ATR" if distance is not None else "Distance: N/A",
        f"Stop / invalidation: {number(record.invalidation_price)}",
    ]
    lines.extend(f"{target.name}: {number(target.price)}" for target in targets)
    lines.extend(
        [
            f"Projected R:R: {number(risk.get('best_r'))}",
            f"Status: {record.status}",
            f"Expires: {_utc_timestamp(record.expires_at)}",
            f"Reason: {escape(record.entry_reason or entry.get('reason') or 'Structural retest')}",
            "Paper execution only. Real orders: 🔒 DISABLED",
        ]
    )
    return "\n".join(lines)


def _utc_timestamp(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_directional_stats(data: dict[str, Any]) -> str:
    coverage = data["coverage"]
    funnel = data["funnel"]
    scores = data["scores"]
    rejections = list(data["rejections"]["primary"].items())[:3]
    highest = data["top_evaluations"][0] if data["top_evaluations"] else None
    lines = [
        "<b>DIRECTIONAL — 24H</b>",
        "",
        f"Scans: {coverage['cycles']}",
        f"Evaluations: {coverage['evaluated_markets']}",
        "",
        f"Actionable: {funnel['ACTIONABLE']}",
        f"Pending: {funnel['PENDING_ENTRY']}",
        f"Watchlist: {funnel['WATCHLIST']}",
        f"No Trade: {funnel['NO_TRADE']}",
        "",
        f"Score ≥80: {scores['gte_80']}",
        f"Score ≥85: {scores['gte_85']}",
        f"Score ≥90: {scores['gte_90']}",
        "",
        "Top rejection reasons:",
    ]
    lines.extend(
        f"{index}. {escape(reason.replace('_', ' ').title())}: {count}"
        for index, (reason, count) in enumerate(rejections, 1)
    )
    if not rejections:
        lines.append("No audited rejections yet.")
    lines.extend(["", f"Near misses: {len(data['near_misses'])}"])
    if highest:
        lines.extend(
            [
                "",
                "Highest score:",
                f"{escape(highest['symbol'])} {highest['market'].title()} "
                f"{highest['direction']} — {highest['score']:.1f}",
                "Rejected: " + escape(highest["primary_rejection_reason"] or "No rejection"),
            ]
        )
    if not data["audit_available"]:
        lines.extend(["", "Evaluation audits begin with this deployment."])
    lines.extend(["", "Observational only. Trading Execution: 🔒 DISABLED"])
    return "\n".join(lines)


def format_near_misses(items: list[dict[str, Any]]) -> str:
    lines = [
        "<b>NEAR MISSES — DIAGNOSTIC ONLY</b>",
        "NOT A SIGNAL — NOT AN ENTRY RECOMMENDATION",
    ]
    if not items:
        lines.extend(["", "No audited near misses in the selected window."])
    for index, item in enumerate(items[:10], 1):
        entry = item.get("entry_zone") or {}
        failures = item.get("hard_filter_reasons") or []
        lines.extend(
            [
                "",
                f"#{index} <b>{escape(item['symbol'])}</b> · "
                f"{item['market'].title()} {item['direction']}",
                f"Score: {item['score']:.1f}",
                f"Current: {number(item.get('current_price'))}",
                f"Entry: {number(entry.get('low'))}–{number(entry.get('high'))}",
                "Failed: " + escape("; ".join(failures) or "N/A"),
            ]
        )
    lines.extend(["", "Trading Execution: 🔒 DISABLED"])
    return "\n".join(lines)


def format_top(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<b>NO TRADE</b>\n\nNo current setup meets configured minimum quality requirements."
    actionable = [item for item in items if _entry_distance(item["signal"]) == 0]
    pending = [item for item in items if item not in actionable]
    blocks = ["🔥 <b>ACTIONABLE NOW</b>"]
    blocks.extend(format_signal(item["signal"], index) for index, item in enumerate(actionable, 1))
    if not actionable:
        blocks.append("None currently. Entry confirmation always requires fresh revalidation.")
    blocks.append("🟡 <b>PENDING ENTRY</b>")
    blocks.extend(format_pending(item["signal"], index) for index, item in enumerate(pending, 1))
    if not pending:
        blocks.append("None currently.")
    return "\n\n".join(blocks)


def _entry_distance(record: SignalRecord) -> float | None:
    metrics = record.payload.get("supporting_metrics") or {}
    price = record.current_monitored_price or metrics.get("current_price")
    atr = metrics.get("atr")
    if not isinstance(price, (int, float)) or not isinstance(atr, (int, float)) or atr <= 0:
        return None
    if record.entry_low is None or record.entry_high is None:
        return None
    if price < record.entry_low:
        raw = record.entry_low - price
    elif price > record.entry_high:
        raw = price - record.entry_high
    else:
        raw = 0
    return raw / atr


def format_position(position: PaperPositionRecord) -> str:
    return (
        f"<b>{escape(position.symbol)} {position.direction}</b>\n"
        f"{position.exchange.title()} {position.market.title()} · {position.status}\n"
        f"Paper entry: {number(position.simulated_fill_price)}\n"
        f"Remaining: {number(position.remaining_quantity)}\n"
        f"Current stop: {number(position.current_stop)}\n"
        f"Paper P&L: {money(position.realized_pnl + position.unrealized_pnl)} USDT"
    )


def format_portfolio(account: PaperAccountRecord, positions: list[PaperPositionRecord]) -> str:
    return (
        "📊 <b>PAPER PORTFOLIO</b>\n\n"
        f"Virtual starting balance: {money(account.starting_balance)} USDT\n"
        f"Cash: {money(account.current_cash)} USDT\n"
        f"Equity: <b>{money(account.current_equity)} USDT</b>\n"
        f"Realized P&L: {money(account.realized_pnl)} USDT\n"
        f"Unrealized P&L: {money(account.unrealized_pnl)} USDT\n"
        f"Fees: {money(account.fees)} USDT\n"
        f"Open paper positions: {sum(x.status == 'OPEN' for x in positions)}\n\n"
        "Virtual values only. No exchange balance is connected."
    )


def format_report(report: PerformanceReport) -> str:
    m = report.metrics
    return (
        f"📅 <b>PAPER TRADING {report.report_type} REPORT</b>\n\n"
        f"Starting equity: {money(m.starting_equity)} USDT\n"
        f"Ending equity: {money(m.ending_equity)} USDT\n"
        f"Net P&L: <b>{money(m.net_pnl)} USDT</b> ({m.return_pct}%)\n"
        f"Closed trades: {m.closed_trades} · Open: {m.open_positions}\n"
        f"Wins: {m.winning_trades} · Losses: {m.losing_trades} · Win rate: {m.win_rate}%\n"
        f"Profit factor: {m.profit_factor if m.profit_factor is not None else 'N/A'}\n"
        f"Average R: {m.average_r}\nFees: {money(m.fees)} USDT\n"
        f"Max drawdown: {money(m.max_drawdown)} USDT"
    )


def money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}"


def number(value: object) -> str:
    if value is None:
        return "N/A"
    return f"{Decimal(str(value)):,.8f}".rstrip("0").rstrip(".")


def format_arbitrage(record: ArbitrageOpportunityRecord, rank: int | None = None) -> str:
    economics = record.payload.get("economics", {})
    prefix = f"#{rank} " if rank else "⚡ "
    return (
        f"{prefix}<b>ARBITRAGE OPPORTUNITY</b>\n\n"
        f"<b>{escape(record.symbol)}</b> · {record.arbitrage_type.replace('_', ' ').title()}\n"
        f"BUY: {record.buy_exchange.title()} {record.buy_market.title()} "
        f"@ {number(economics.get('buy_vwap'))}\n"
        f"SELL: {record.sell_exchange.title()} {record.sell_market.title()} "
        f"@ {number(economics.get('sell_vwap'))}\n"
        f"Target notional: {money(Decimal(str(record.target_notional)))} USDT\n"
        f"Gross spread: {number(record.gross_spread_pct)}%\n"
        f"Fees: -{number(economics.get('fees_pct'))}%\n"
        f"Estimated slippage: -{number(economics.get('slippage_pct'))}%\n"
        f"Safety buffer: -{number(economics.get('safety_buffer_pct'))}%\n"
        f"Expected net edge: <b>{number(record.net_edge_pct)}%</b>\n"
        f"Estimated paper P&L: {money(Decimal(str(record.expected_net_pnl)))} USDT\n"
        f"Confidence: {record.confidence_score:.1f}/100\nStatus: {record.status}\n\n"
        "Opportunity-quality score is not profit probability. Paper simulation only. "
        "No real orders placed."
    )


def format_arbitrage_top(records: list[ArbitrageOpportunityRecord]) -> str:
    if not records:
        return (
            "<b>NO ARBITRAGE</b>\n\nNo current opportunity meets configured quality requirements."
        )
    return "\n\n".join(
        ["⚡ <b>TOP ARBITRAGE OPPORTUNITIES</b>"]
        + [format_arbitrage(row, index) for index, row in enumerate(records[:5], 1)]
    )


def format_paper_arbitrage(position: PaperArbitragePositionRecord) -> str:
    return (
        f"<b>{escape(position.symbol)} PAPER ARBITRAGE</b>\n"
        f"{position.arbitrage_type.replace('_', ' ').title()} · {position.status}\n"
        f"Notional: {money(Decimal(str(position.notional)))} USDT\n"
        f"Paper net P&L: {money(Decimal(str(position.realized_pnl)))} USDT\n"
        "Two-leg virtual simulation. No real orders."
    )
