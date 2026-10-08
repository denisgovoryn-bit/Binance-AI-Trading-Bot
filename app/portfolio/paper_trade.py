"""Idempotent multi-user paper processing of Phase 4 monitoring events."""

from datetime import UTC, datetime
from decimal import Decimal

from app.config.settings import Settings
from app.db.portfolio_models import (
    PaperAccountRecord,
    PaperFillRecord,
    PaperPositionRecord,
    PortfolioSnapshotRecord,
    TelegramUserRecord,
)
from app.db.repositories.portfolio import PortfolioRepository
from app.db.signal_models import SignalMonitoringEventRecord, SignalRecord
from app.portfolio.fees import apply_slippage, apply_stop_slippage, calculate_fee, fee_bps
from app.portfolio.models import PaperFillType, PaperPositionStatus, PaperProfile, PaperTradeMode
from app.portfolio.pnl import gross_pnl, unrealized_pnl
from app.portfolio.sizing import size_position


class PaperTradeService:
    """Fan one global signal event into isolated virtual accounts."""

    def __init__(self, repository: PortfolioRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    async def process(self, signal: SignalRecord, event: SignalMonitoringEventRecord) -> int:
        users = await self.repository.eligible_users()
        processed = 0
        for user in users:
            user_processed = False
            if not self._matches(user, signal):
                continue
            account = await self.repository.active_account(user.id)
            if account is None:
                continue
            position = await self.repository.position_for_signal(account.id, signal.id)
            if event.event_type == "ENTRY_CONFIRMED":
                if position is None:
                    await self._open(user, account, signal, event)
                    processed += 1
                    user_processed = True
            elif position is not None and not await self.repository.has_fill(position.id, event.id):
                if event.event_type.startswith("TP") and event.event_type.endswith("_REACHED"):
                    await self._target(account, position, signal, event)
                    processed += 1
                    user_processed = True
                elif event.event_type == "STOP_REACHED":
                    await self._stop(account, position, signal, event)
                    processed += 1
                    user_processed = True
                elif event.event_type == "STOP_UPDATED" and event.configured_price is not None:
                    position.current_stop = Decimal(str(event.configured_price))
                    processed += 1
                    user_processed = True
                elif event.event_type == "SIGNAL_COMPLETED" and position.remaining_quantity == 0:
                    position.status = PaperPositionStatus.COMPLETED.value
                    position.closed_at = event.exchange_timestamp
            if user_processed:
                await self._snapshot(account, event.exchange_timestamp, event.event_type)
        await self.repository.commit()
        return processed

    async def mark_to_market(
        self, account: PaperAccountRecord, prices: dict[str, Decimal], at: datetime | None = None
    ) -> None:
        positions = await self.repository.list_positions(account.id, PaperPositionStatus.OPEN.value)
        total = Decimal("0")
        for position in positions:
            price = prices.get(position.symbol)
            if price is None:
                continue
            position.unrealized_pnl = unrealized_pnl(
                position.direction,
                position.simulated_fill_price,
                price,
                position.remaining_quantity,
            )
            total += position.unrealized_pnl
        account.unrealized_pnl = total
        self._equity(account)
        await self._snapshot(account, at or datetime.now(UTC), "MARK_TO_MARKET")
        await self.repository.commit()

    async def _open(
        self,
        user: TelegramUserRecord,
        account: PaperAccountRecord,
        signal: SignalRecord,
        event: SignalMonitoringEventRecord,
    ) -> PaperPositionRecord:
        assert event.price is not None and signal.invalidation_price is not None
        reference = Decimal(str(event.price))
        bullish = signal.direction in {"BUY", "LONG"}
        fill_price = apply_slippage(reference, bullish, True, self.settings)
        profile = PaperProfile(user.preferences.paper_profile)
        size = size_position(
            account.current_equity, fill_price, profile, signal.market, self.settings
        )
        fee = calculate_fee(
            fill_price, size.quantity, fee_bps(signal.exchange, signal.market, self.settings)
        )
        slippage = abs(fill_price - reference) * size.quantity
        position = PaperPositionRecord(
            account_id=account.id,
            signal_id=signal.id,
            exchange=signal.exchange,
            market=signal.market,
            symbol=signal.symbol,
            direction=signal.direction,
            timeframe=signal.timeframe,
            setup_type=signal.setup_type,
            confidence=Decimal(str(signal.current_confidence)),
            opened_at=event.exchange_timestamp,
            reference_entry_price=reference,
            simulated_fill_price=fill_price,
            initial_stop=Decimal(str(signal.invalidation_price)),
            current_stop=Decimal(str(signal.invalidation_price)),
            targets_json=signal.payload.get("targets", []),
            simulated_notional=size.notional,
            initial_quantity=size.quantity,
            remaining_quantity=size.quantity,
            closed_quantity=Decimal("0"),
            realized_pnl=-fee,
            unrealized_pnl=Decimal("0"),
            fees=fee,
            simulated_slippage=slippage,
            sizing_method=size.method,
            leverage=size.leverage,
            status=PaperPositionStatus.OPEN.value,
        )
        position.fills = []
        await self.repository.add_position(position)
        await self.repository.add_fill(
            PaperFillRecord(
                position=position,
                event_id=event.id,
                fill_type=PaperFillType.ENTRY.value,
                occurred_at=event.exchange_timestamp,
                reference_price=reference,
                fill_price=fill_price,
                quantity=size.quantity,
                gross_pnl=Decimal("0"),
                fee=fee,
                slippage=slippage,
                net_pnl=-fee,
                allocation_pct=Decimal("100"),
            )
        )
        account.fees += fee
        account.simulated_slippage += slippage
        account.realized_pnl -= fee
        account.current_cash -= fee
        self._equity(account)
        return position

    async def _target(
        self,
        account: PaperAccountRecord,
        position: PaperPositionRecord,
        signal: SignalRecord,
        event: SignalMonitoringEventRecord,
    ) -> None:
        assert event.price is not None
        name = event.event_type.removesuffix("_REACHED")
        allocation = self._allocation(signal, name)
        quantity = (position.initial_quantity * allocation / Decimal("100")).quantize(
            Decimal("0.00000001")
        )
        quantity = min(quantity, position.remaining_quantity)
        await self._exit(
            account,
            position,
            event,
            PaperFillType(name),
            Decimal(str(event.price)),
            Decimal(str(event.price)),
            quantity,
            allocation,
        )
        if position.remaining_quantity == 0:
            position.status = PaperPositionStatus.COMPLETED.value
            position.closed_at = event.exchange_timestamp

    async def _stop(
        self,
        account: PaperAccountRecord,
        position: PaperPositionRecord,
        signal: SignalRecord,
        event: SignalMonitoringEventRecord,
    ) -> None:
        assert event.price is not None
        reference = Decimal(str(event.configured_price or event.price))
        fill = apply_stop_slippage(reference, signal.direction in {"BUY", "LONG"}, self.settings)
        await self._exit(
            account,
            position,
            event,
            PaperFillType.STOP,
            reference,
            fill,
            position.remaining_quantity,
            Decimal("100"),
        )
        position.status = PaperPositionStatus.STOPPED.value
        position.closed_at = event.exchange_timestamp

    async def _exit(
        self,
        account: PaperAccountRecord,
        position: PaperPositionRecord,
        event: SignalMonitoringEventRecord,
        fill_type: PaperFillType,
        reference: Decimal,
        fill: Decimal,
        quantity: Decimal,
        allocation: Decimal,
    ) -> None:
        gross = gross_pnl(position.direction, position.simulated_fill_price, fill, quantity)
        fee = calculate_fee(
            fill, quantity, fee_bps(position.exchange, position.market, self.settings)
        )
        slippage = abs(fill - reference) * quantity
        net = gross - fee
        await self.repository.add_fill(
            PaperFillRecord(
                position=position,
                event_id=event.id,
                fill_type=fill_type.value,
                occurred_at=event.exchange_timestamp,
                reference_price=reference,
                fill_price=fill,
                quantity=quantity,
                gross_pnl=gross,
                fee=fee,
                slippage=slippage,
                net_pnl=net,
                allocation_pct=allocation,
            )
        )
        position.remaining_quantity -= quantity
        position.closed_quantity += quantity
        position.realized_pnl += net
        position.fees += fee
        position.simulated_slippage += slippage
        account.unrealized_pnl -= position.unrealized_pnl
        position.unrealized_pnl = unrealized_pnl(
            position.direction, position.simulated_fill_price, fill, position.remaining_quantity
        )
        account.unrealized_pnl += position.unrealized_pnl
        account.realized_pnl += net
        account.current_cash += net
        account.fees += fee
        account.simulated_slippage += slippage
        self._equity(account)

    async def _snapshot(self, account: PaperAccountRecord, at: datetime, reason: str) -> None:
        await self.repository.add_snapshot(
            PortfolioSnapshotRecord(
                account=account,
                captured_at=at,
                cash=account.current_cash,
                equity=account.current_equity,
                realized_pnl=account.realized_pnl,
                unrealized_pnl=account.unrealized_pnl,
                fees=account.fees,
                drawdown=account.current_drawdown,
                reason=reason,
            )
        )

    @staticmethod
    def _equity(account: PaperAccountRecord) -> None:
        account.current_equity = account.current_cash + account.unrealized_pnl
        account.peak_equity = max(account.peak_equity, account.current_equity)
        drawdown = account.current_equity - account.peak_equity
        account.current_drawdown = drawdown
        account.max_drawdown = min(account.max_drawdown, drawdown)

    @staticmethod
    def _allocation(signal: SignalRecord, name: str) -> Decimal:
        plan = signal.payload.get("paper_exit_plan", [])
        found = next((x["percentage"] for x in plan if x["target_name"] == name), None)
        if found is not None:
            return Decimal(str(found))
        count = max(1, len(signal.targets))
        return Decimal("100") / Decimal(count)

    @staticmethod
    def _matches(user: TelegramUserRecord, signal: SignalRecord) -> bool:
        pref = user.preferences
        return (
            pref.paper_trade_mode == PaperTradeMode.AUTO_PAPER.value
            and signal.exchange in pref.enabled_exchanges
            and signal.market in pref.enabled_markets
            and Decimal(str(signal.current_confidence)) >= pref.minimum_confidence
            and (not pref.selected_symbols or signal.symbol in pref.selected_symbols)
            and (not pref.selected_timeframes or signal.timeframe in pref.selected_timeframes)
        )
