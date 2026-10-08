"""Sanitized read-only directional scanner decision report."""

import asyncio
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.db.session import Database
from app.signals.diagnostics import directional_diagnostics

SCAN_PATTERN = re.compile(
    r"symbols=(?P<symbols>\d+).*actionable=(?P<actionable>\d+)"
    r"(?: pending=(?P<pending>\d+))?.*watchlist=(?P<watchlist>\d+)"
    r".*no_trade=(?P<no_trade>\d+)"
)


def legacy_log_coverage(log_file: str, cutoff: datetime) -> dict[str, Any]:
    paths = sorted(Path(log_file).parent.glob(Path(log_file).name + "*"))
    cycles: list[tuple[datetime, dict[str, int]]] = []
    for path in paths:
        for line in path.read_text(errors="ignore").splitlines():
            try:
                row = json.loads(line)
                at = datetime.fromisoformat(row["asctime"])
                message = row.get("message", "")
            except (ValueError, KeyError, TypeError):
                continue
            if at < cutoff or not message.startswith("Directional scan complete:"):
                continue
            match = SCAN_PATTERN.search(message)
            if match:
                cycles.append(
                    (
                        at,
                        {key: int(value or 0) for key, value in match.groupdict().items()},
                    )
                )
    totals: Counter[str] = Counter()
    partial = 0
    for _, counts in cycles:
        totals.update(counts)
        classified = (
            counts["actionable"] + counts["pending"] + counts["watchlist"] + counts["no_trade"]
        )
        partial += classified != counts["symbols"]
    return {
        "cycles": len(cycles),
        "first_cycle": cycles[0][0].isoformat() if cycles else None,
        "last_cycle": cycles[-1][0].isoformat() if cycles else None,
        "totals": dict(totals),
        "partial_cycles": partial,
        "limitation": (
            "Legacy logs contain aggregate winning-direction outcomes only; score, component, "
            "venue, candidate-direction, and rejection-detail recovery is impossible."
        ),
    }


async def main() -> None:
    settings = Settings()
    database = Database(settings)
    now = datetime.now()
    try:
        async with database.sessions() as session:
            audited = await directional_diagnostics(session, hours=24)
        report = {
            "window_hours": 24,
            "legacy_log_coverage": legacy_log_coverage(
                settings.runtime_log_file, now - timedelta(hours=24)
            ),
            "audited_since_diagnostics_deployment": audited,
            "trading_execution": "DISABLED",
        }
        print(json.dumps(report, default=str, indent=2))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
