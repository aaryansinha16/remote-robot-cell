from cloud.main import create_app
from fastapi.testclient import TestClient

from .conftest import snapshot


def test_job_api_round_trip(settings):
    app = create_app(settings, connect=False)
    with TestClient(app) as client:
        services = app.state.services
        client.portal.call(services.fleet.apply_robot, "site-001", snapshot())

        body = {"robot_id": "robot-1", "pick": "A", "place": "B"}
        created = client.post("/api/jobs", json=body, headers={"Idempotency-Key": "k1"})
        repeated = client.post("/api/jobs", json=body, headers={"Idempotency-Key": "k1"})
        assert (created.status_code, repeated.status_code) == (201, 200)
        assert created.json()["job_id"] == repeated.json()["job_id"]
        assert created.json()["status"] == "pending"

        same_station = client.post("/api/jobs", json={**body, "place": "A"})
        assert same_station.status_code == 422

        job_id = created.json()["job_id"]
        cancel = client.post(f"/api/jobs/{job_id}/cancel")
        assert cancel.status_code == 202
        assert cancel.json()["cancel_requested"] is True

        assert client.get("/api/jobs/missing").status_code == 404

        with client.websocket_connect("/api/ws") as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot"
            assert [job["job_id"] for job in first["data"]["jobs"]] == [job_id]
