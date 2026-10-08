"""Telegram-safe formatting for real read-only account data."""

from app.accounts.models import ExchangeAccount


def format_connections(
    accounts: tuple[ExchangeAccount, ...],
    telegram: str = "CONNECTED",
    scanner: dict[str, object] | None = None,
) -> str:
    rows = {row.exchange: row for row in accounts}

    def state(name: str) -> str:
        row = rows.get(name)
        if row is None:
            return "⚪ Disabled"
        if row.permission_confirmed:
            return "✅ Read-only"
        if row.trading_permission_detected:
            return "⚠️ Connected; trading permission detected (unused)"
        return "❌ Unavailable"

    runtime = scanner or {}
    return (
        "<b>SYSTEM CONNECTIONS</b>\n\n"
        f"Telegram\n{'✅ Connected' if telegram == 'CONNECTED' else '❌ Unavailable'}\n\n"
        "Binance Market Data\n✅ Configured\n\nBinance Account\n"
        + state("BINANCE")
        + "\n\nBybit Market Data\n✅ Configured\n\nBybit Account\n"
        + state("BYBIT")
        + "\n\nDirectional scanner\n"
        + f"{runtime.get('directional_status', 'NOT_STARTED')} "
        + f"({runtime.get('directional_symbols', 0)} symbols)\n\n"
        + "Arbitrage scanner\n"
        + f"{runtime.get('arbitrage_status', 'NOT_STARTED')} "
        + f"({runtime.get('arbitrage_symbols', 0)} symbols)\n\n"
        + f"Public Binance\n{runtime.get('public_binance', 'NOT_STARTED')}\n\n"
        + f"Public Bybit\n{runtime.get('public_bybit', 'NOT_STARTED')}\n\n"
        + f"Last universe refresh\n{runtime.get('last_universe_refresh', 'never')}\n\n"
        + "Trading Execution\n🔒 Disabled"
    )


def format_account(account: ExchangeAccount) -> str:
    title = f"<b>{account.exchange} — REAL ACCOUNT — READ ONLY</b>"
    if account.error or account.status.value in {"DISABLED", "AUTHENTICATION_FAILED"}:
        return (
            f"{title}\n\nAccount unavailable ({account.status.value})."
            "\n\nNo trading actions available."
        )
    balances = [x for x in account.balances if x.available or x.locked or x.wallet or x.equity]
    balance_text = (
        "\n".join(
            f"{x.asset}: available {x.available}, locked {x.locked}"
            + (f", wallet {x.wallet}, equity {x.equity}" if x.wallet is not None else "")
            for x in balances
        )
        or "No non-zero balances."
    )
    return (
        f"{title}\n\n<b>Balances</b>\n{balance_text}\n\n"
        f"Open positions: {len(account.positions)}\n"
        f"Open orders: {len(account.orders)}\n\nNo trading actions available."
    )


def format_positions(accounts: tuple[ExchangeAccount, ...]) -> str:
    lines = ["<b>REAL POSITIONS — READ ONLY</b>"]
    for account in accounts:
        for p in account.positions:
            lines.append(
                f"\n<b>{p.exchange} {p.symbol}</b>\n{p.direction} {p.quantity}\n"
                f"Entry: {p.entry_price or 'n/a'} | Mark: {p.mark_price or 'n/a'}\n"
                f"Unrealized P&amp;L: {p.unrealized_pnl or 'n/a'}\n"
                f"Leverage: {p.leverage or 'n/a'} | "
                f"Liquidation: {p.liquidation_price or 'n/a'}"
            )
    if len(lines) == 1:
        lines.append("\nNo open real positions.")
    lines.append("\n\nTrading execution is disabled.")
    return "".join(lines)
