"""Wire contract.

Mirrors `packages/shared-types/src/index.ts` field for field. Python attributes
are snake_case; `to_camel` aliasing produces the camelCase JSON the frontend
expects, so no transformation code is needed at either boundary.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --------------------------------------------------------------------------- #
# Industrial hierarchy
# --------------------------------------------------------------------------- #


class EquipmentType(StrEnum):
    TORQUE_TOOL = "TORQUE_TOOL"
    PLC = "PLC"
    SENSOR = "SENSOR"
    ROBOT = "ROBOT"
    CONVEYOR = "CONVEYOR"


class Factory(WireModel):
    factory_id: str
    name: str
    country: str


class Line(WireModel):
    line_id: str
    factory_id: str
    name: str


class Station(WireModel):
    station_id: str
    line_id: str
    name: str


class Equipment(WireModel):
    equipment_id: str
    station_id: str
    type: EquipmentType
    name: str


class Process(WireModel):
    process_id: str
    station_id: str
    name: str
    operation: str


# --------------------------------------------------------------------------- #
# Quality and metrics
# --------------------------------------------------------------------------- #


class QualityResult(StrEnum):
    OK = "OK"
    NOK = "NOK"


class MetricKey(StrEnum):
    FPY = "fpy"
    NOK_RATE = "nokRate"
    TORQUE_NOK_RATE = "torqueNokRate"
    CYCLE_TIME = "cycleTime"
    TOOL_ERROR_RATE = "toolErrorRate"


class MetricPoint(WireModel):
    timestamp: datetime
    station_id: str
    metric: MetricKey
    value: float
    unit: str


# Wire unit per metric. Part of the contract `MetricPoint.unit` carries, so it
# lives with the model rather than with any one provider.
METRIC_UNITS: dict[MetricKey, str] = {
    MetricKey.FPY: "%",
    MetricKey.NOK_RATE: "%",
    MetricKey.TORQUE_NOK_RATE: "%",
    MetricKey.CYCLE_TIME: "s",
    MetricKey.TOOL_ERROR_RATE: "%",
}


# --------------------------------------------------------------------------- #
# Industrial events
# --------------------------------------------------------------------------- #


class IndustrialEventType(StrEnum):
    MAINTENANCE = "MAINTENANCE"
    TOOL_ERROR = "TOOL_ERROR"
    PLC_ERROR = "PLC_ERROR"
    SENSOR_FAULT = "SENSOR_FAULT"
    MATERIAL_CHANGE = "MATERIAL_CHANGE"
    SHIFT_CHANGE = "SHIFT_CHANGE"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class IndustrialEvent(WireModel):
    event_id: str
    timestamp: datetime
    station_id: str
    equipment_id: str | None = None
    type: IndustrialEventType
    severity: RiskLevel
    message: str


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


class EvidenceType(StrEnum):
    METRIC_SHIFT = "METRIC_SHIFT"
    EVENT_CORRELATION = "EVENT_CORRELATION"
    HISTORICAL_PATTERN = "HISTORICAL_PATTERN"
    PROCESS_CONTEXT = "PROCESS_CONTEXT"


class Evidence(WireModel):
    id: str
    type: EvidenceType
    source: str
    value: str | float
    relevance: float
    timestamp: datetime | None = None


# --------------------------------------------------------------------------- #
# Anomaly
# --------------------------------------------------------------------------- #


class AnomalyStatus(StrEnum):
    NEW = "NEW"
    ANALYZING = "ANALYZING"
    CONFIRMED = "CONFIRMED"
    ACTION_CREATED = "ACTION_CREATED"
    RESOLVED = "RESOLVED"


class AnomalyDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"


class Anomaly(WireModel):
    id: str
    timestamp: datetime
    factory_id: str
    line_id: str
    station_id: str
    metric: MetricKey
    baseline: float
    actual: float
    delta: float
    delta_ratio: float
    direction: AnomalyDirection
    z_score: float
    severity: float
    status: AnomalyStatus
    window_start: datetime
    window_end: datetime
    evidence: list[Evidence] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 4M1E (spec §11)
# --------------------------------------------------------------------------- #


class FourMOneECategory(StrEnum):
    MAN = "MAN"
    MACHINE = "MACHINE"
    MATERIAL = "MATERIAL"
    METHOD = "METHOD"
    ENVIRONMENT = "ENVIRONMENT"


# --------------------------------------------------------------------------- #
# FMEA (spec §8, §12)
# --------------------------------------------------------------------------- #


class ActionPriority(StrEnum):
    HIGH = "H"
    MEDIUM = "M"
    LOW = "L"


class FmeaMatchStatus(StrEnum):
    """Match precedence, fixed by spec §8.1. `NO_MATCH` is terminal — the engine
    must never paper over a miss with an invented record (spec §8.2)."""

    EXACT = "EXACT"
    NORMALIZED = "NORMALIZED"
    SEMANTIC = "SEMANTIC"
    LLM_CANDIDATE = "LLM_CANDIDATE"
    NO_MATCH = "NO_MATCH"


class FmeaRecord(WireModel):
    process: str
    operation: str
    failure_mode: str
    effect: str
    cause: str
    prevention_control: str | None = None
    detection_control: str | None = None
    severity: int
    occurrence: int
    detection: int
    rpn: int
    ap: ActionPriority
    recommended_action: str | None = None
    category: FourMOneECategory | None = None


class FmeaCandidate(WireModel):
    record: FmeaRecord
    score: float
    match_type: FmeaMatchStatus


class FmeaMatchResult(WireModel):
    match_status: FmeaMatchStatus
    requires_human_review: bool
    candidates: list[FmeaCandidate] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Root cause and risk (spec §14, §15)
# --------------------------------------------------------------------------- #


class RootCauseCandidate(WireModel):
    cause: str
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)
    category: FourMOneECategory | None = None


class RiskAssessment(WireModel):
    severity: int
    occurrence: int
    detection: int
    rpn: int
    ap: ActionPriority


class RecommendedAction(WireModel):
    action: str
    priority: RiskLevel
    rationale: str | None = None
    # Evidence the recommendation is grounded in. An AI provider must cite ids
    # that exist in the anomaly's evidence; a citation to a source that is not
    # there is rejected outright rather than shown (spec §7). Deterministic
    # producers leave this empty.
    evidence_ids: list[str] = Field(default_factory=list)


class AiAnalysisResult(WireModel):
    summary: str
    root_cause_candidates: list[RootCauseCandidate]
    risk: RiskAssessment
    recommended_actions: list[RecommendedAction]
    provider: str
    generated_at: datetime


# --------------------------------------------------------------------------- #
# Analysis surface (spec §15, §25)
# --------------------------------------------------------------------------- #


class AnalysisRequest(WireModel):
    """The client posts the anomaly it received from `/api/anomalies`, evidence
    included (spec §15 input). `process_id` is optional context."""

    anomaly: Anomaly
    process_id: str | None = None


class AnalysisResponse(WireModel):
    """Everything Page 4 (AI Analysis) and Page 5 (FMEA & Risk) need in one shot:
    the anomaly, the FMEA match, and the AI explanation."""

    anomaly: Anomaly
    fmea_match: FmeaMatchResult
    analysis: AiAnalysisResult


class FmeaMatchRequest(WireModel):
    failure_mode: str
    process: str | None = None
    operation: str | None = None


# --------------------------------------------------------------------------- #
# Action loop and verification (spec §16 Page 6, §25)
# --------------------------------------------------------------------------- #


class ActionItemStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class ActionItem(WireModel):
    id: str
    anomaly_id: str
    title: str
    owner: str
    due_date: datetime
    status: ActionItemStatus
    priority: RiskLevel


class Verification(WireModel):
    action_id: str
    verified_at: datetime
    metric: MetricKey
    before: float
    after: float
    target: float
    passed: bool


class ActionCreateRequest(WireModel):
    """The client echoes the anomaly it is acting on. The engine keeps that
    snapshot, so verification can re-measure the same station and metric later
    without a second lookup or a client-supplied measurement."""

    anomaly: Anomaly
    title: str
    owner: str
    due_date: datetime
    priority: RiskLevel = RiskLevel.MEDIUM


class ActionUpdateRequest(WireModel):
    status: ActionItemStatus | None = None
    owner: str | None = None
    due_date: datetime | None = None


class ActionWithContext(WireModel):
    """Board read model: the action record plus the anomaly it answers and the
    latest verification, so Page 6 renders in one round trip."""

    action: ActionItem
    anomaly: Anomaly
    latest_verification: Verification | None = None


class VerifyActionResponse(WireModel):
    action: ActionItem
    verification: Verification