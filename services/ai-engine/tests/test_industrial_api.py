"""Smoke tests for the industrial data surface.

These lock in two properties the demo depends on: the camelCase wire format
shared with `@factorymind/shared-types`, and bit-identical metric values across
repeated queries (spec §10 reproducibility).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_reports_active_providers(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dataProvider"] == "mock"
    assert body["aiProvider"] == "mock"


def test_healthz_is_a_dependency_free_liveness_probe(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    # Liveness must not depend on any provider, so it stays out of the API contract.
    assert "/healthz" not in client.get("/openapi.json").json()["paths"]


def test_factory_hierarchy_uses_camel_case_keys(client: TestClient) -> None:
    factory = client.get("/api/factories").json()[0]
    assert factory["factoryId"] == "FAC-DEMO-01"

    line = client.get("/api/lines", params={"factoryId": factory["factoryId"]}).json()[0]
    assert line["lineId"] == "LINE-A"

    stations = client.get("/api/stations", params={"lineId": line["lineId"]}).json()
    assert [station["stationId"] for station in stations][0] == "ST-A10"
    assert all(station["lineId"] == "LINE-A" for station in stations)


def test_station_exposes_equipment_and_processes(client: TestClient) -> None:
    equipment = client.get("/api/equipment", params={"stationId": "ST-A40"}).json()
    assert {item["equipmentId"] for item in equipment} == {"EQ-A40-01", "EQ-A40-02", "EQ-A40-03"}
    assert equipment[0]["type"] == "TORQUE_TOOL"

    processes = client.get("/api/processes", params={"stationId": "ST-A40"}).json()
    assert len(processes) == 2


def test_metrics_are_reproducible(client: TestClient) -> None:
    end = datetime(2026, 10, 1, 14, 0, 0)
    start = end - timedelta(hours=1)
    params = {
        "metric": "fpy",
        "from": start.isoformat(),
        "to": end.isoformat(),
        "stationId": "ST-A40",
    }

    first = client.get("/api/metrics", params=params).json()
    second = client.get("/api/metrics", params=params).json()

    assert first == second
    # Both ends of the window are inclusive: 13:00 … 14:00 at 5-minute buckets.
    assert len(first) == 13
    assert first[0]["unit"] == "%"
    assert first[0]["stationId"] == "ST-A40"


def test_metrics_reject_inverted_window(client: TestClient) -> None:
    response = client.get(
        "/api/metrics",
        params={
            "metric": "fpy",
            "from": "2026-10-01T14:00:00",
            "to": "2026-10-01T10:00:00",
        },
    )
    assert response.status_code == 400


def test_events_endpoint_returns_the_engineered_chain(client: TestClient) -> None:
    params = {"from": "2026-10-01T06:00:00", "to": "2026-10-01T14:00:00"}

    response = client.get("/api/events", params=params)
    assert response.status_code == 200

    events = response.json()
    assert events, "the demo timeline must expose the causal events behind the fault"
    assert [item["timestamp"] for item in events] == sorted(
        item["timestamp"] for item in events
    )
    assert {item["stationId"] for item in events} == {"ST-A40", "ST-B30"}
    # Reproducible, exactly like the metric series.
    assert events == client.get("/api/events", params=params).json()


def test_events_can_be_filtered_by_type(client: TestClient) -> None:
    response = client.get(
        "/api/events",
        params={
            "from": "2026-10-01T06:00:00",
            "to": "2026-10-01T14:00:00",
            "types": "TOOL_ERROR",
        },
    )
    assert response.status_code == 200
    assert {item["type"] for item in response.json()} == {"TOOL_ERROR"}


def test_events_reject_an_unknown_type(client: TestClient) -> None:
    response = client.get(
        "/api/events",
        params={
            "from": "2026-10-01T06:00:00",
            "to": "2026-10-01T14:00:00",
            "types": "NOT_A_REAL_TYPE",
        },
    )
    assert response.status_code == 400


def test_events_are_absent_between_incidents(client: TestClient) -> None:
    response = client.get(
        "/api/events",
        params={"from": "2026-10-01T09:00:00", "to": "2026-10-01T12:00:00"},
    )
    assert response.json() == []