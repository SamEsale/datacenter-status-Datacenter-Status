from fastapi.testclient import TestClient
import importlib.util
from pathlib import Path

BACKEND_MAIN = Path(__file__).resolve().parents[1] / "app.py"
spec = importlib.util.spec_from_file_location("app_module", BACKEND_MAIN)
app_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(app_module)


def test_get_status(monkeypatch):
    monkeypatch.setenv("CACHET_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("CACHET_API_TOKEN", "dummy-token")

    def fake_fetch_components(base_url, token):
        return [
            {"id": 1, "name": "Provider A", "status": 1, "status_name": "Operational"},
            {"id": 2, "name": "Provider B", "status": 1, "status_name": "Operational"},
        ]

    def fake_fetch_active_incidents(base_url, token):
        return [
            {
                "id": 100,
                "name": "Incident 1",
                "status": 1,
                "components": [{"id": 2}],
                "updated_at": "2026-01-26 10:00:00",
            }
        ]

    def fake_fetch_recent_incidents_for_modal(base_url, token):
        return [
            {
                "id": 100,
                "name": "Incident 1",
                "status": 1,
                "components": [{"id": 2}],
                "updated_at": "2026-01-26 10:00:00",
                "message": "Last update(s):\n2026-01-26 10:00 – Something happened",
            },
            {
                "id": 101,
                "name": "Incident 2",
                "status": 4,
                "components": [{"id": 1}],
                "updated_at": "2026-01-25 10:00:00",
                "message": "Last update(s):\n2026-01-25 10:00 – Resolved",
            },
        ]

    monkeypatch.setattr(app_module, "fetch_components", fake_fetch_components)
    monkeypatch.setattr(app_module, "fetch_active_incidents", fake_fetch_active_incidents)
    monkeypatch.setattr(app_module, "fetch_recent_incidents_for_modal", fake_fetch_recent_incidents_for_modal)

    client = TestClient(app_module.app)
    response = client.get("/status")

    assert response.status_code == 200
    data = response.json()

    b = next(x for x in data["data"] if x["id"] == 2)
    assert b["status"] == "Down"
    assert b["incident_ids"] == [100]

    a = next(x for x in data["data"] if x["id"] == 1)
    assert a["status"] == "Operational"
    assert a["incident_ids"] == [101]

    assert "incidents" in data
    assert isinstance(data["incidents"], list)

