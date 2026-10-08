"""Replay deterministic public price fixtures through a stored signal; no order access."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.monitoring.models import PriceEvent
from app.monitoring.signal_monitor import SignalMonitor


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("signal_id", help="Persisted signal UUID")
    parser.add_argument("--events", type=Path, help="JSON array of normalized price events")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Generate entry and target-crossing events from stored levels",
    )
    return parser.parse_args()


def event_from_json(item: dict[str, Any]) -> PriceEvent:
    exchange_at = datetime.fromisoformat(str(item["exchange_timestamp"]))
    received_at = datetime.fromisoformat(str(item.get("received_at", item["exchange_timestamp"])))
    return PriceEvent(
        symbol=str(item["symbol"]),
        last=float(item["last"]),
        bid=float(item["bid"]) if item.get("bid") is not None else None,
        ask=float(item["ask"]) if item.get("ask") is not None else None,
        exchange_timestamp=exchange_at,
        received_at=received_at,
    )


async def main() -> None:
    args = arguments()
    settings = Settings()
    database = Database(settings)
    async with database.sessions() as session:
        repo = SignalRepository(session)
        record = await repo.get_signal(args.signal_id)
        if record is None:
            raise SystemExit(f"signal {args.signal_id} not found")
        if args.demo:
            now = datetime.now(UTC)
            entry = record.ideal_entry or ((record.entry_low or 0) + (record.entry_high or 0)) / 2
            prices = [entry, *(target.price for target in record.targets)]
            raw = [
                PriceEvent(
                    symbol=record.symbol,
                    last=price,
                    bid=price,
                    ask=price,
                    exchange_timestamp=now + timedelta(seconds=index),
                    received_at=now + timedelta(seconds=index),
                )
                for index, price in enumerate(prices)
            ]
        elif args.events:
            raw = [event_from_json(item) for item in json.loads(args.events.read_text())]
        else:
            raise SystemExit("provide --events PATH or --demo")
        monitor = SignalMonitor(repo, settings)
        for event in raw:
            result = await monitor.process_price(record, event)
            print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        print(f"final_status={record.status} events={len(record.monitoring_events)}")
    await database.close()


if __name__ == "__main__":
    asyncio.run(main())
