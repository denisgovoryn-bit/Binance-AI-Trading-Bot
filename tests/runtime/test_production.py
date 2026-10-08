import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.logging import configure_logging
from app.market.models import ExchangeName
from app.runtime.health import HealthSupervisor, SystemStatus
from app.runtime.lock import AlreadyRunningError, ProcessLock
from app.runtime.scanners import ScannerRuntimeState


def test_process_lock_prevents_duplicate_instances_and_removes_pid(tmp_path) -> None:
    path = tmp_path / "bot.pid"
    with ProcessLock(str(path)):
        assert path.read_text().strip().isdigit()
        with pytest.raises(AlreadyRunningError):
            with ProcessLock(str(path)):
                pass
    assert not path.exists()


def test_rotating_logs_are_persistent_and_secret_redacted(tmp_path, monkeypatch) -> None:
    secret = "never-log-this-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    path = tmp_path / "bot.log"
    configure_logging("INFO", log_file=str(path), max_bytes=200, backup_count=2)
    logger = logging.getLogger("rotation-test")
    for _ in range(20):
        logger.info("message %s %s", secret, "x" * 40)
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = "".join(item.read_text() for item in tmp_path.glob("bot.log*"))
    assert "[REDACTED]" in content
    assert secret not in content
    assert list(tmp_path.glob("bot.log.*"))


@pytest.mark.asyncio
async def test_alerts_are_deduplicated_and_recovery_is_reported(tmp_path) -> None:
    sent: list[str] = []
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'health.db'}",
        telegram_admin_ids=(123,),
        runtime_alert_cooldown_seconds=900,
    )

    async def probe() -> bool:
        return True

    async def sender(_chat_id: int, text: str) -> int:
        sent.append(text)
        return 1

    supervisor = HealthSupervisor(
        settings,
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        probe,
        sender,
    )
    supervisor.status.database = "UNAVAILABLE"
    supervisor.status.telegram = "CONNECTED"
    supervisor.status.binance_public = "CONNECTED"
    supervisor.status.bybit_public = "CONNECTED"
    supervisor.status.bybit_private = "READ_ONLY_PERMISSION_CONFIRMED"
    supervisor.status.directional_scanner = "RUNNING"
    supervisor.status.arbitrage_scanner = "RUNNING"
    await supervisor.evaluate_incidents()
    await supervisor.evaluate_incidents()
    assert len(sent) == 1 and "SYSTEM ALERT" in sent[0]
    supervisor.incidents["database"].opened_at = datetime.now(UTC) - timedelta(seconds=5)
    supervisor.status.database = "CONNECTED"
    await supervisor.evaluate_incidents()
    assert len(sent) == 2 and "SYSTEM RECOVERED" in sent[1] and "Downtime" in sent[1]
    supervisor.status.database = "UNAVAILABLE"
    await supervisor.evaluate_incidents()
    assert len(sent) == 2


def test_service_configs_restart_after_crash_and_do_not_embed_secrets() -> None:
    root = Path(__file__).parents[2]
    launchd = (root / "deploy/macos/com.crypto-signal-bot.plist.template").read_text()
    systemd = (root / "deploy/linux/crypto-signal-bot.service").read_text()
    assert "KeepAlive" in launchd and "RunAtLoad" in launchd and "__PROJECT_DIR__" in launchd
    assert "Restart=on-failure" in systemd and "RestartSec=10" in systemd
    assert "API_KEY" not in launchd + systemd and "BOT_TOKEN" not in launchd + systemd


def test_scanner_watchdog_marks_stale_scan_degraded() -> None:
    settings = Settings(directional_full_scan_minutes=15, runtime_scanner_stale_multiplier=2)
    scanner = SimpleNamespace(
        state=ScannerRuntimeState(
            directional_status="RUNNING",
            last_directional_scan=datetime.now(UTC) - timedelta(minutes=31),
        )
    )
    supervisor = HealthSupervisor(
        settings,
        SimpleNamespace(),  # type: ignore[arg-type]
        scanner,  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
    )
    assert supervisor._directional_health(datetime.now(UTC)) == "DEGRADED"


def test_public_websocket_watchdog_detects_stale_exchange() -> None:
    settings = Settings(runtime_websocket_stale_seconds=60)
    state = ScannerRuntimeState(
        arbitrage_status="RUNNING",
        public_binance="CONNECTED",
        public_bybit="CONNECTED",
        binance_last_websocket_event=datetime.now(UTC),
        bybit_last_websocket_event=datetime.now(UTC) - timedelta(seconds=61),
    )
    supervisor = HealthSupervisor(
        settings,
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(state=state),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
    )
    assert supervisor._public_health(ExchangeName.BINANCE, datetime.now(UTC)) == "CONNECTED"
    assert supervisor._public_health(ExchangeName.BYBIT, datetime.now(UTC)) == "DEGRADED"


def test_system_status_is_explicitly_read_only() -> None:
    status_text = SystemStatus(universe_size=83).telegram_text()
    assert "Trading Execution: 🔒 DISABLED" in status_text
    assert "Universe size: 83" in status_text
