from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app


def test_health_reports_database_and_unstarted_services() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
    assert payload["exchanges"]["binance_spot"] == "not_started"
    assert payload["active_signals"] == 0
