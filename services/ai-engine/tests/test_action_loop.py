"""Action loop and verification tests (spec §16 Page 6, §25).

Verification re-measures the metric at the moment it runs, so the two verdicts
are exercised against a frozen clock: 09:00 sits in a healthy stretch, 15:00 sits
on the plateau of the 13:30–16:00 incident. Freezing the clock — rather than
letting the wall clock pick — is what makes the pass/fail assertions reproducible.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from app.config import load_anomaly_rules
from app.domain.models import MetricKey, MetricPoint
from app.engine.verify import measure_recovery, robust_level, within_recovery_band
from app.main import create_app
from fastapi.testclient import TestClient

# 09:00–17:00 contains exactly one incident (13:30–16:00), on ST-A40.
INCIDENT_WINDOW = {"from": "2026-10-01T09:00:00", "to": "2026-10-01T17:00:00"}


class _FrozenDatetime(datetime):
    """A `datetime` whose `now()` is pinned, so the recovery window is fixed."""

    frozen: datetime = datetime(2026, 10, 1, 9, 0, 0)

    @classmethod
    def now(cls, tz=None) -> datetime:  # noqa: ARG003 - signature must match
        return cls.frozen


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def freeze(monkeypatch: pytest.MonkeyPatch):
    def _freeze(moment: datetime) -> None:
        _FrozenDatetime.frozen = moment
        monkeypatch.setattr("app.routers.actions.datetime", _FrozenDatetime)

    return _freeze


def _fpy_anomaly(client: TestClient) -> dict:
    body = client.get("/api/anomalies", params=INCIDENT_WINDOW).json()
    return next(item for item in body if item["metric"] == "fpy")


def _create(client: TestClient, anomaly: dict, **overrides) -> dict:
    payload = {
        "anomaly": anomaly,
        "title": "复核拧紧枪扭矩标定并重新验证",
        "owner": "张工",
        "dueDate": "2026-10-05T17:00:00",
        "priority": "HIGH",
    }
    payload.update(overrides)
    response = client.post("/api/actions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _points(values: list[float], metric: MetricKey = MetricKey.FPY) -> list[MetricPoint]:
    base = datetime(2026, 10, 1, 8, 0, 0)
    return [
        MetricPoint(
            timestamp=base + timedelta(minutes=5 * index),
            station_id="ST-A40",
            metric=metric,
            value=value,
            unit="%",
        )
        for index, value in enumerate(values)
    ]


# --------------------------------------------------------------------------- #
# Deterministic recovery measurement
# --------------------------------------------------------------------------- #


def test_robust_level_is_the_median_not_the_mean() -> None:
    # One wild bucket must not move the level the verdict is read from.
    assert robust_level(_points([99.6, 99.6, 99.6, 99.6, 80.0])) == 99.6


def test_recovery_band_respects_the_metric_bad_direction() -> None:
    # FPY: bad direction is DOWN, so recovery means climbing back to the band.
    assert within_recovery_band(direction="DOWN", after=99.5, target=99.6, tolerance=0.3)
    assert not within_recovery_band(direction="DOWN", after=97.4, target=99.6, tolerance=0.3)
    # Cycle time: bad direction is UP, so recovery means dropping back into it.
    assert within_recovery_band(direction="UP", after=48.2, target=48.0, tolerance=1.0)
    assert not within_recovery_band(direction="UP", after=51.5, target=48.0, tolerance=1.0)


def test_measure_recovery_refuses_to_judge_on_too_few_samples() -> None:
    rules = load_anomaly_rules()
    measurement = measure_recovery(
        _points([99.6, 99.6]),
        metric_rule=rules.metrics["fpy"],
        target=99.6,
        min_points=rules.verification.min_points,
    )
    # An absent measurement must never be reported as a passing one.
    assert measurement is None


# --------------------------------------------------------------------------- #
# Action lifecycle
# --------------------------------------------------------------------------- #


def test_create_returns_an_open_action_wired_to_the_anomaly(client: TestClient) -> None:
    anomaly = _fpy_anomaly(client)
    action = _create(client, anomaly)

    assert action["id"] == "ACT-0001"
    assert action["anomalyId"] == anomaly["id"]
    assert action["status"] == "OPEN"
    assert action["priority"] == "HIGH"
    assert action["owner"] == "张工"


def test_board_returns_the_action_with_its_anomaly_context(client: TestClient) -> None:
    anomaly = _fpy_anomaly(client)
    _create(client, anomaly)

    board = client.get("/api/actions").json()
    assert len(board) == 1
    entry = board[0]
    assert entry["action"]["id"] == "ACT-0001"
    assert entry["anomaly"]["stationId"] == "ST-A40"
    assert entry["anomaly"]["metric"] == "fpy"
    # Nothing has been verified yet, so the board carries no verdict.
    assert entry["latestVerification"] is None


def test_status_advances_and_is_filterable(client: TestClient) -> None:
    anomaly = _fpy_anomaly(client)
    _create(client, anomaly)

    started = client.patch("/api/actions/ACT-0001", json={"status": "IN_PROGRESS"})
    assert started.status_code == 200
    assert started.json()["status"] == "IN_PROGRESS"

    assert len(client.get("/api/actions", params={"status": "OPEN"}).json()) == 0
    assert len(client.get("/api/actions", params={"status": "IN_PROGRESS"}).json()) == 1


def test_unknown_action_is_a_404(client: TestClient) -> None:
    assert client.patch("/api/actions/ACT-9999", json={"status": "DONE"}).status_code == 404
    assert client.post("/api/actions/ACT-9999/verify").status_code == 404


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


def test_verify_passes_once_the_metric_is_back_in_band(client: TestClient, freeze) -> None:
    # 08:00–09:00 is between incidents, so the re-measurement reads healthy.
    freeze(datetime(2026, 10, 1, 9, 0, 0))
    anomaly = _fpy_anomaly(client)
    _create(client, anomaly)

    body = client.post("/api/actions/ACT-0001/verify").json()

    assert body["verification"]["passed"] is True
    assert body["verification"]["before"] == anomaly["actual"]
    assert body["verification"]["target"] == anomaly["baseline"]
    assert body["verification"]["after"] >= anomaly["baseline"] - 0.3
    # A passing verification closes the action.
    assert body["action"]["status"] == "DONE"


def test_verify_fails_while_the_excursion_persists(client: TestClient, freeze) -> None:
    # 14:00–15:00 sits on the incident plateau: the fault is still live.
    freeze(datetime(2026, 10, 1, 15, 0, 0))
    anomaly = _fpy_anomaly(client)
    _create(client, anomaly)

    body = client.post("/api/actions/ACT-0001/verify").json()

    assert body["verification"]["passed"] is False
    assert body["verification"]["after"] < anomaly["baseline"]
    # An action that has not taken effect stays open for follow-up.
    assert body["action"]["status"] == "OPEN"


def test_verification_is_reproducible_at_a_fixed_clock(client: TestClient, freeze) -> None:
    freeze(datetime(2026, 10, 1, 9, 0, 0))
    anomaly = _fpy_anomaly(client)
    _create(client, anomaly)

    first = client.post("/api/actions/ACT-0001/verify").json()["verification"]
    second = client.post("/api/actions/ACT-0001/verify").json()["verification"]

    assert first["after"] == second["after"]
    assert first["passed"] == second["passed"]


def test_board_exposes_the_latest_verification(client: TestClient, freeze) -> None:
    freeze(datetime(2026, 10, 1, 9, 0, 0))
    anomaly = _fpy_anomaly(client)
    _create(client, anomaly)
    client.post("/api/actions/ACT-0001/verify")

    entry = client.get("/api/actions").json()[0]
    assert entry["latestVerification"]["passed"] is True
    assert entry["latestVerification"]["metric"] == "fpy"