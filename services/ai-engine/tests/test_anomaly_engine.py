"""Tests for the anomaly engine (spec §13, §43).

The mock source carries a recurring engineered incident (see
`app/providers/mock.py`), so detection is asserted against a *known* fault
rather than a hand-tuned series. The engine's episode logic is additionally
exercised on synthetic series where the expected grouping is unambiguous.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from app.config import load_anomaly_rules
from app.domain.models import MetricKey, MetricPoint
from app.engine import detect_anomalies
from app.main import create_app
from fastapi.testclient import TestClient

# 09:00–17:00 contains exactly one incident (13:30–16:00) and no other.
INCIDENT_WINDOW = {"from": "2026-10-01T09:00:00", "to": "2026-10-01T17:00:00"}
# 10:00–12:00 sits between incidents, so the engine must stay silent.
QUIET_WINDOW = {"from": "2026-10-01T10:00:00", "to": "2026-10-01T12:00:00"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _anomalies(client: TestClient, **params: str) -> list[dict]:
    response = client.get("/api/anomalies", params=params)
    assert response.status_code == 200
    return response.json()


def _series(
    values: list[float],
    *,
    metric: MetricKey = MetricKey.CYCLE_TIME,
    unit: str = "s",
) -> list[MetricPoint]:
    start = datetime(2026, 10, 1, 0, 0, 0)
    return [
        MetricPoint(
            timestamp=start + timedelta(minutes=5 * index),
            station_id="ST-X1",
            metric=metric,
            value=value,
            unit=unit,
        )
        for index, value in enumerate(values)
    ]


def _detect(points: list[MetricPoint]) -> list:
    return detect_anomalies(
        points,
        rules=load_anomaly_rules(),
        factory_id="FAC-DEMO-01",
        station_lines={"ST-X1": "LINE-A"},
    )


# --------------------------------------------------------------------------- #
# Detection against the engineered incident
# --------------------------------------------------------------------------- #


def test_quiet_window_reports_nothing(client: TestClient) -> None:
    assert _anomalies(client, **QUIET_WINDOW) == []


def test_one_incident_yields_one_anomaly_per_metric(client: TestClient) -> None:
    body = _anomalies(client, **INCIDENT_WINDOW)
    keys = [(item["stationId"], item["metric"]) for item in body]

    # One fault, one record per metric — not one record per 5-minute bucket.
    assert len(keys) == len(set(keys)) == 4
    assert set(keys) == {
        ("ST-A40", "fpy"),
        ("ST-A40", "cycleTime"),
        ("ST-A40", "torqueNokRate"),
        ("ST-B30", "cycleTime"),
    }


def test_fpy_anomaly_matches_the_engineered_shift(client: TestClient) -> None:
    fpy = next(item for item in _anomalies(client, **INCIDENT_WINDOW) if item["metric"] == "fpy")

    assert fpy["stationId"] == "ST-A40"
    assert fpy["lineId"] == "LINE-A"
    assert fpy["factoryId"] == "FAC-DEMO-01"
    assert fpy["direction"] == "DOWN"
    assert fpy["status"] == "NEW"
    assert fpy["delta"] < 0
    assert -0.03 < fpy["deltaRatio"] < -0.01
    assert fpy["zScore"] > 3.0
    assert fpy["severity"] > 0.8
    assert fpy["windowStart"] < fpy["windowEnd"]


def test_evidence_explains_the_shift(client: TestClient) -> None:
    fpy = next(item for item in _anomalies(client, **INCIDENT_WINDOW) if item["metric"] == "fpy")
    evidence = fpy["evidence"]

    assert evidence[0]["type"] == "METRIC_SHIFT"
    assert evidence[0]["relevance"] == 1.0
    assert evidence[0]["source"] == "一次合格率 FPY"
    assert "↓" in evidence[0]["value"]
    # The torque tool is the co-moving signal the spec §16 Page-4 example shows.
    assert any(item["source"] == "扭矩不合格率" for item in evidence[1:])
    assert all(0.0 <= item["relevance"] <= 1.0 for item in evidence)


def test_tool_error_corroborates_without_becoming_an_anomaly(client: TestClient) -> None:
    body = _anomalies(client, **INCIDENT_WINDOW)
    fpy = next(item for item in body if item["metric"] == "fpy")
    sources = {item["source"] for item in fpy["evidence"]}

    # Spec §16 Page 4 explains the FPY drop with torque, cycle time *and* tool
    # error together.
    assert {"扭矩不合格率", "节拍", "工具故障率"} <= sources
    # ...but a tool-error excursion is a symptom of the torque fault, not a
    # second incident to triage, so it stays out of the anomaly list.
    assert ("ST-A40", "toolErrorRate") not in {
        (item["stationId"], item["metric"]) for item in body
    }


def test_evidence_cites_the_equipment_events(client: TestClient) -> None:
    fpy = next(item for item in _anomalies(client, **INCIDENT_WINDOW) if item["metric"] == "fpy")
    events = [item for item in fpy["evidence"] if item["type"] == "EVENT_CORRELATION"]

    assert events, "a torque-tool fault must cite the events on its own station"
    # Oldest first, so the maintenance action reads as cause and the alarm as
    # effect — the chain spec §10.3 asks for.
    assert [item["source"] for item in events] == ["保养事件", "工具报警", "PLC 报警"]
    stamps = [item["timestamp"] for item in events]
    assert stamps == sorted(stamps)
    # Exponential decay: the oldest link is weak, never worthless — a precursor
    # that explains the fault must not be scored as zero-relevance evidence.
    assert all(0.0 < item["relevance"] <= 1.0 for item in events)


def test_metric_evidence_precedes_the_event_narrative(client: TestClient) -> None:
    fpy = next(item for item in _anomalies(client, **INCIDENT_WINDOW) if item["metric"] == "fpy")
    kinds = [item["type"] for item in fpy["evidence"]]

    assert kinds[0] == "METRIC_SHIFT"
    assert kinds.index("EVENT_CORRELATION") == kinds.count("METRIC_SHIFT")


def test_anomalies_are_ranked_by_severity(client: TestClient) -> None:
    severities = [item["severity"] for item in _anomalies(client, **INCIDENT_WINDOW)]
    assert severities == sorted(severities, reverse=True)


def test_anomalies_are_reproducible(client: TestClient) -> None:
    assert _anomalies(client, **INCIDENT_WINDOW) == _anomalies(client, **INCIDENT_WINDOW)


def test_scope_filter_narrows_the_result(client: TestClient) -> None:
    body = _anomalies(client, **INCIDENT_WINDOW, lineId="LINE-B")
    assert body
    assert {item["lineId"] for item in body} == {"LINE-B"}


def test_inverted_window_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/api/anomalies",
        params={"from": "2026-10-01T17:00:00", "to": "2026-10-01T09:00:00"},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Episode grouping
# --------------------------------------------------------------------------- #

_CLEAN = [49.9, 50.1]


def test_short_gaps_are_bridged_into_one_episode() -> None:
    values = _CLEAN * 10 + [51.0, 51.0] + _CLEAN + [51.0, 51.0] + _CLEAN * 10

    anomalies = _detect(_series(values))

    assert len(anomalies) == 1


def test_long_gaps_split_episodes() -> None:
    values = _CLEAN * 10 + [51.0, 51.0] + _CLEAN * 5 + [51.0, 51.0] + _CLEAN * 10

    anomalies = _detect(_series(values))

    assert len(anomalies) == 2


def test_lone_blip_is_discarded() -> None:
    values = _CLEAN * 10 + [51.0] + _CLEAN * 10

    assert _detect(_series(values)) == []


def test_improvement_is_not_an_anomaly() -> None:
    # FPY rising is good news; only the configured bad direction is flagged.
    values = [99.5, 99.7] * 10 + [101.5, 101.5] + [99.5, 99.7] * 10

    assert _detect(_series(values, metric=MetricKey.FPY, unit="%")) == []