from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app


def test_empty_read_only_signal_api(tmp_path) -> None:
    app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}"))
    with TestClient(app) as client:
        assert client.get("/signals").json()["count"] == 0
        top = client.get("/signals/top")
        assert top.status_code == 200
        assert top.json() == {
            "status": "NO_TRADE",
            "count": 0,
            "actionable_now": [],
            "pending_entry": [],
            "watchlist": [],
            "signals": [],
            "message": "No current setup meets configured quality requirements.",
        }
        assert client.get("/signals/pending").json() == {"count": 0, "signals": []}
        analytics = client.get("/signals/pending/analytics")
        assert analytics.status_code == 200
        assert analytics.json()["pending_setups_created"] == 0
        diagnostics = client.get("/signals/diagnostics?window=24h")
        assert diagnostics.status_code == 200
        assert diagnostics.json()["audit_available"] is False
        assert client.get("/signals/diagnostics?window=forever").status_code == 422
        assert client.get("/signals/missing").status_code == 404
        monitoring = client.get("/signals/monitoring")
        assert monitoring.status_code == 200
        assert monitoring.json()["websocket_connection_state"] == "NOT_STARTED"
        assert client.get("/signals/missing/live").status_code == 404
        assert client.get("/signals/missing/events").status_code == 404
