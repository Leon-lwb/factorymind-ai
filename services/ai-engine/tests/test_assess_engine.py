"""Tests for the Assess stage — FMEA matching, ranking and risk (spec §8, §12, §14, §43).

The deterministic levels are exercised directly against the synthetic Master
FMEA so the precedence chain is unambiguous; the ranking is asserted end-to-end
through the HTTP surface, against the same engineered incident the anomaly tests
use, so the two stages are proven to compose.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta

import pytest
from app.config import load_anomaly_rules, load_assess_rules
from app.domain.models import (
    ActionPriority,
    Anomaly,
    AnomalyDirection,
    AnomalyStatus,
    Evidence,
    EvidenceType,
    FmeaCandidate,
    FmeaMatchResult,
    FmeaMatchStatus,
    MetricKey,
)
from app.engine import (
    FmeaQuery,
    compute_action_priority,
    compute_rpn,
    load_master_fmea,
    match_fmea,
    rank_root_causes,
    risk_from_match,
    severity_from_anomaly,
)
from app.main import create_app
from app.providers.ai import AiAnalysisRequest, MockAiProvider
from fastapi.testclient import TestClient

# 09:00–17:00 contains exactly one incident (13:30–16:00), on ST-A40.
INCIDENT_WINDOW = {"from": "2026-10-01T09:00:00", "to": "2026-10-01T17:00:00"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _records():
    return load_master_fmea(load_assess_rules())


def _fpy_anomaly(client: TestClient) -> dict:
    body = client.get("/api/anomalies", params=INCIDENT_WINDOW).json()
    return next(item for item in body if item["metric"] == "fpy")


def _anomaly(**overrides) -> Anomaly:
    moment = datetime(2026, 10, 1, 14, 0, 0)
    base = {
        "id": "AN-ST-A40-fpy-20261001T1400",
        "timestamp": moment,
        "factory_id": "FAC-DEMO-01",
        "line_id": "LINE-A",
        "station_id": "ST-A40",
        "metric": MetricKey.FPY,
        "baseline": 99.6,
        "actual": 97.4,
        "delta": -2.2,
        "delta_ratio": -0.0221,
        "direction": AnomalyDirection.DOWN,
        "z_score": 4.5,
        "severity": 0.9,
        "status": AnomalyStatus.NEW,
        "window_start": moment,
        "window_end": moment + timedelta(minutes=30),
        "evidence": [],
    }
    base.update(overrides)
    return Anomaly(**base)


def _evidence(evidence_id: str, kind: str, source: str, relevance: float) -> Evidence:
    return Evidence(
        id=evidence_id,
        type=EvidenceType(kind),
        source=source,
        value="test",
        relevance=relevance,
        timestamp=datetime(2026, 10, 1, 14, 0, 0),
    )


# --------------------------------------------------------------------------- #
# Loading and deterministic derivation
# --------------------------------------------------------------------------- #


def test_master_fmea_derives_rpn_and_ap() -> None:
    record = next(
        item
        for item in _records()
        if item.cause == "拧紧枪扭矩标定漂移" and item.failure_mode == "一次合格率下降"
    )

    # RPN and AP are not stored in the file — they are derived from S/O/D.
    assert record.rpn == record.severity * record.occurrence * record.detection == 96
    assert record.ap is ActionPriority.MEDIUM


# --------------------------------------------------------------------------- #
# Match precedence (spec §8.1)
# --------------------------------------------------------------------------- #


def test_exact_match_is_scoped_to_the_process() -> None:
    result = match_fmea(
        FmeaQuery(failure_mode="一次合格率下降", process="关键扭矩拧紧"),
        _records(),
        load_assess_rules(),
    )

    assert result.match_status is FmeaMatchStatus.EXACT
    assert result.requires_human_review is False
    assert result.candidates
    assert {item.record.process for item in result.candidates} == {"关键扭矩拧紧"}
    assert {item.match_type for item in result.candidates} == {FmeaMatchStatus.EXACT}
    assert {item.score for item in result.candidates} == {1.0}


def test_process_scope_excludes_other_stations() -> None:
    rules = load_assess_rules()
    scoped = match_fmea(
        FmeaQuery(failure_mode="一次合格率下降", process="关键扭矩拧紧"), _records(), rules
    )
    unscoped = match_fmea(FmeaQuery(failure_mode="一次合格率下降"), _records(), rules)

    assert {item.record.process for item in scoped.candidates} == {"关键扭矩拧紧"}
    # Without the process key the same failure mode also pulls in the re-check
    # and underbody stations — which is exactly why the route passes the process.
    assert {item.record.process for item in unscoped.candidates} != {"关键扭矩拧紧"}


def test_normalized_match_ignores_spacing_and_width() -> None:
    # An internal space survives `strip`, so this cannot be an EXACT hit; the
    # normalizer is what collapses it onto the stored failure mode.
    result = match_fmea(
        FmeaQuery(failure_mode="一次合格率 下降"), _records(), load_assess_rules()
    )

    assert result.match_status is FmeaMatchStatus.NORMALIZED
    assert result.requires_human_review is False
    assert {item.match_type for item in result.candidates} == {FmeaMatchStatus.NORMALIZED}
    assert {item.score for item in result.candidates} == {0.9}


def test_semantic_match_accepts_a_near_synonym() -> None:
    result = match_fmea(
        FmeaQuery(failure_mode="一次合格率降低"), _records(), load_assess_rules()
    )

    assert result.match_status is FmeaMatchStatus.SEMANTIC
    assert result.requires_human_review is True
    assert result.candidates
    assert all(0.0 < item.score < 1.0 for item in result.candidates)
    assert {item.record.failure_mode for item in result.candidates} == {"一次合格率下降"}


def test_exact_precedes_semantic() -> None:
    rules = load_assess_rules()
    exact = match_fmea(FmeaQuery(failure_mode="一次合格率下降"), _records(), rules)
    semantic = match_fmea(FmeaQuery(failure_mode="一次合格率降低"), _records(), rules)

    assert exact.match_status is FmeaMatchStatus.EXACT
    assert semantic.match_status is FmeaMatchStatus.SEMANTIC


def test_unmatched_failure_mode_is_no_match_and_never_fabricates() -> None:
    result = match_fmea(
        FmeaQuery(failure_mode="环境温度异常升高"), _records(), load_assess_rules()
    )

    assert result.match_status is FmeaMatchStatus.NO_MATCH
    assert result.requires_human_review is True
    assert result.candidates == []


def test_llm_candidate_level_is_reachable_and_flagged() -> None:
    records = _records()

    def generator(query: FmeaQuery, pool) -> list[FmeaCandidate]:
        return [
            FmeaCandidate(
                record=pool[0], score=0.5, match_type=FmeaMatchStatus.LLM_CANDIDATE
            )
        ]

    result = match_fmea(
        FmeaQuery(failure_mode="环境温度异常升高"),
        records,
        load_assess_rules(),
        candidate_generator=generator,
    )

    assert result.match_status is FmeaMatchStatus.LLM_CANDIDATE
    assert result.requires_human_review is True
    assert result.candidates[0].match_type is FmeaMatchStatus.LLM_CANDIDATE


def test_without_a_generator_the_llm_level_stays_off() -> None:
    # The default path must not conjure a candidate from nothing.
    result = match_fmea(
        FmeaQuery(failure_mode="环境温度异常升高"), _records(), load_assess_rules()
    )

    assert result.match_status is FmeaMatchStatus.NO_MATCH
    assert result.candidates == []


# --------------------------------------------------------------------------- #
# Risk scoring (spec §12, §15)
# --------------------------------------------------------------------------- #


def test_rpn_and_action_priority_thresholds() -> None:
    rules = load_assess_rules().action_priority

    assert compute_rpn(8, 4, 3) == 96
    # Severity 8 is at the medium bar, RPN 96 is under the high bar.
    assert compute_action_priority(8, 4, 3, rules) is ActionPriority.MEDIUM
    # Severity 9 alone forces HIGH, whatever the RPN.
    assert compute_action_priority(9, 1, 1, rules) is ActionPriority.HIGH
    # RPN 200 alone forces HIGH, whatever the severity.
    assert compute_action_priority(8, 5, 5, rules) is ActionPriority.HIGH
    assert compute_action_priority(4, 3, 4, rules) is ActionPriority.LOW


def test_severity_from_anomaly_maps_onto_the_ten_point_scale() -> None:
    assert severity_from_anomaly(0.0) == 1
    assert severity_from_anomaly(0.9) == 9
    assert severity_from_anomaly(1.0) == 10


def test_risk_falls_back_when_nothing_matched() -> None:
    rules = load_assess_rules()
    match = FmeaMatchResult(
        match_status=FmeaMatchStatus.NO_MATCH,
        requires_human_review=True,
        candidates=[],
    )

    risk = risk_from_match(match, _anomaly(), rules)

    # Severity comes from the anomaly, O/D from the conservative fallback.
    assert risk.severity == 9
    assert risk.occurrence == rules.fallback_risk.occurrence
    assert risk.detection == rules.fallback_risk.detection
    assert risk.rpn == compute_rpn(risk.severity, risk.occurrence, risk.detection)


# --------------------------------------------------------------------------- #
# Root-cause ranking (spec §14)
# --------------------------------------------------------------------------- #


def test_root_causes_rank_the_machine_cause_first(client: TestClient) -> None:
    response = client.post("/api/analysis", json={"anomaly": _fpy_anomaly(client)})
    assert response.status_code == 200
    body = response.json()

    assert body["fmeaMatch"]["matchStatus"] == "EXACT"
    candidates = body["analysis"]["rootCauseCandidates"]
    assert candidates
    # The torque-tool calibration drift is corroborated by tool-error evidence;
    # the method and material causes of the same failure mode are not.
    assert candidates[0]["cause"] == "拧紧枪扭矩标定漂移"
    assert candidates[0]["category"] == "MACHINE"
    confidences = [item["confidence"] for item in candidates]
    assert confidences == sorted(confidences, reverse=True)
    assert candidates[0]["confidence"] > candidates[1]["confidence"]


def test_root_cause_confidence_is_bounded(client: TestClient) -> None:
    body = client.post("/api/analysis", json={"anomaly": _fpy_anomaly(client)}).json()
    for candidate in body["analysis"]["rootCauseCandidates"]:
        assert 0.0 <= candidate["confidence"] <= 1.0


def test_machine_cause_beats_a_method_cause_with_no_supporting_evidence() -> None:
    rules = load_assess_rules()
    records = _records()
    match = match_fmea(
        FmeaQuery(failure_mode="一次合格率下降", process="关键扭矩拧紧"), records, rules
    )
    anomaly = _anomaly(
        evidence=[
            # Only machine-flavoured evidence is present.
            _evidence("EV-1", "METRIC_SHIFT", "工具故障率", 0.24),
            _evidence("EV-2", "EVENT_CORRELATION", "工具报警", 0.5),
        ]
    )

    candidates = rank_root_causes(
        anomaly=anomaly,
        evidence=anomaly.evidence,
        match=match,
        records=records,
        rules=rules,
        anomaly_rules=load_anomaly_rules(),
    )

    by_cause = {item.cause: item for item in candidates}
    machine = by_cause["拧紧枪扭矩标定漂移"]
    method = by_cause["拧紧程序扭矩参数被误改"]
    assert machine.confidence > method.confidence
    # Supporting evidence is ordered strongest-first.
    assert machine.evidence_ids == ["EV-2", "EV-1"]
    assert method.evidence_ids == []


# --------------------------------------------------------------------------- #
# AI explanation layer (spec §15, §21)
# --------------------------------------------------------------------------- #


def test_analysis_returns_risk_actions_and_provider(client: TestClient) -> None:
    body = client.post("/api/analysis", json={"anomaly": _fpy_anomaly(client)}).json()
    analysis = body["analysis"]

    assert analysis["provider"] == "mock"
    assert analysis["summary"]
    assert analysis["risk"]["severity"] == 8
    assert analysis["risk"]["ap"] == "M"
    assert analysis["recommendedActions"]
    assert analysis["recommendedActions"][0]["priority"] == "MEDIUM"


def test_mock_ai_does_not_invent_actions_without_a_match() -> None:
    rules = load_assess_rules()
    match = FmeaMatchResult(
        match_status=FmeaMatchStatus.NO_MATCH,
        requires_human_review=True,
        candidates=[],
    )
    anomaly = _anomaly()

    result = asyncio.run(
        MockAiProvider().analyze(
            AiAnalysisRequest(
                anomaly=anomaly,
                evidence=[],
                process=None,
                equipment=None,
                root_causes=[],
                risk=risk_from_match(match, anomaly, rules),
                fmea_match=match,
                metric_label="一次合格率 FPY",
            )
        )
    )

    assert result.root_cause_candidates == []
    assert result.recommended_actions[0].action.startswith("人工确认")
    assert "8.2" in (result.recommended_actions[0].rationale or "")


def test_analysis_is_reproducible(client: TestClient) -> None:
    anomaly = _fpy_anomaly(client)
    first = client.post("/api/analysis", json={"anomaly": anomaly}).json()
    second = client.post("/api/analysis", json={"anomaly": anomaly}).json()

    assert _without_timestamp(first) == _without_timestamp(second)


def test_fmea_match_endpoint() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/fmea/match",
        json={"failureMode": "一次合格率下降", "process": "关键扭矩拧紧"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matchStatus"] == "EXACT"
    assert body["requiresHumanReview"] is False


def test_fmea_records_endpoint_derives_rpn_and_ap() -> None:
    client = TestClient(create_app())
    response = client.get("/api/fmea/records")

    assert response.status_code == 200
    records = response.json()
    assert records
    for record in records:
        # RPN and AP are arithmetic on S/O/D, never read from the file (spec §6.1).
        assert record["rpn"] == record["severity"] * record["occurrence"] * record["detection"]
        assert record["ap"] in {"H", "M", "L"}
        assert record["cause"] and record["failureMode"] and record["process"]


def test_fmea_records_agree_with_the_match_candidates() -> None:
    client = TestClient(create_app())
    records = client.get("/api/fmea/records").json()
    match = client.post(
        "/api/fmea/match",
        json={"failureMode": "一次合格率下降", "process": "关键扭矩拧紧"},
    ).json()

    # One cause can back several failure modes, so the row identity is the triple.
    indexed = {
        (record["process"], record["failureMode"], record["cause"]): record
        for record in records
    }
    assert match["candidates"]
    for candidate in match["candidates"]:
        record = candidate["record"]
        key = (record["process"], record["failureMode"], record["cause"])
        # The reference list and the match must be the same row, RPN/AP included.
        assert record == indexed[key]


def _without_timestamp(body: dict) -> dict:
    """Drop the wall-clock stamp so two runs can be compared for reproducibility."""
    clone = copy.deepcopy(body)
    clone["analysis"].pop("generatedAt", None)
    return json.loads(json.dumps(clone))