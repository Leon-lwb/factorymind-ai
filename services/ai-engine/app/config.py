"""Runtime configuration.

Environment variables carry deployment concerns only — no credentials are ever
hard-coded (spec §31). Detection thresholds and weights live in
`config/anomaly_rules.json` so a domain expert can retune the engine without
touching code (spec §13, §14, §43).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DEMO_SEED = 2026
DEFAULT_AI_TIMEOUT_SECONDS = 30.0
DEFAULT_AI_MAX_RETRIES = 2

# UNS metric-file alias pattern. supOS rejects any alias that does not match
# `^[A-Za-z_$][A-Za-z0-9_$]*$`, so the separator is an underscore and the metric
# is slugged (`METRIC_ALIAS_SLUGS`). Must equal `metricAliasTemplate` in
# `config/uns_model.json`, which seeds the files this template queries.
DEFAULT_METRIC_ALIAS_TEMPLATE = "{station}_{metric}"

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ANOMALY_RULES_PATH = SERVICE_ROOT / "config" / "anomaly_rules.json"
DEFAULT_ASSESS_RULES_PATH = SERVICE_ROOT / "config" / "assess_rules.json"
DEFAULT_MASTER_FMEA_PATH = SERVICE_ROOT / "config" / "master_fmea.json"

DIRECTION_UP = "UP"
DIRECTION_DOWN = "DOWN"


@dataclass(frozen=True, slots=True)
class Settings:
    data_provider: str
    ai_provider: str
    sup_os_api_url: str | None
    sup_os_api_token: str | None
    sup_os_timeout_seconds: float
    sup_os_verify_tls: bool
    sup_os_uns_root_alias: str | None
    sup_os_event_source: str
    sup_os_mqtt_url: str | None
    sup_os_mqtt_topic: str
    sup_os_mqtt_username: str | None
    sup_os_mqtt_password: str | None
    ai_base_url: str | None
    ai_api_key: str | None
    ai_model: str | None
    ai_timeout_seconds: float
    ai_max_retries: int
    demo_seed: int


@dataclass(frozen=True, slots=True)
class MetricRule:
    direction: str
    magnitude_scale: float
    label: str
    unit: str
    # Half-width of the healthy band around the recovery target. A metric counts
    # as recovered once it is back inside `target ± tolerance` on the good side
    # of its bad direction (spec §16 Page 6).
    recovery_tolerance: float


@dataclass(frozen=True, slots=True)
class SeverityWeights:
    z_scale: float
    z_weight: float
    magnitude_weight: float


@dataclass(frozen=True, slots=True)
class EvidenceRules:
    """When a metric counts as corroborating evidence for an anomaly.

    A relative-change test cannot separate a real co-movement from noise: a
    quiet metric with a small scale clears it on a rounding error. The test is
    therefore a z-score against that metric's own robust scale, which is
    comparable across metrics with wildly different units.
    """

    min_correlated_z: float
    full_scale_z: float
    max_correlated_metrics: int
    min_baseline_points: int
    event_lookback_minutes: int
    max_events: int


@dataclass(frozen=True, slots=True)
class VerificationRules:
    """How the recovery measurement is taken (spec §16 Page 6). Kept beside the
    metric rules because the band it compares against is a metric property."""

    window_minutes: int
    min_points: int


@dataclass(frozen=True, slots=True)
class AnomalyRules:
    z_score_threshold: float
    min_episode_points: int
    max_gap_points: int
    severity: SeverityWeights
    evidence: EvidenceRules
    verification: VerificationRules
    metrics: dict[str, MetricRule]


# --------------------------------------------------------------------------- #
# Assess rules (spec §8, §14)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RootCauseWeights:
    """Weights of the deterministic root-cause score (spec §14). Kept in config
    so a domain expert can retune the ranking without touching code."""

    correlation: float
    temporal: float
    magnitude: float
    fmea_relevance: float
    historical_evidence: float


@dataclass(frozen=True, slots=True)
class FmeaMatchingRules:
    semantic_threshold: float
    max_candidates: int


@dataclass(frozen=True, slots=True)
class ActionPriorityRules:
    """Thresholds behind the compact AP rule (see `app.engine.risk`)."""

    high_severity: int
    high_rpn: int
    medium_severity: int
    medium_rpn: int


@dataclass(frozen=True, slots=True)
class FallbackRisk:
    """O/D used when no FMEA record matched — the assessment must still be
    presentable, and it is flagged for human review rather than trusted."""

    occurrence: int
    detection: int


@dataclass(frozen=True, slots=True)
class CauseEvidenceRules:
    """Which evidence a 4M1E category is expected to be corroborated by.

    This is what lets two causes of the same failure mode rank differently: a
    machine cause is supported by tool-error evidence, a material cause is not.
    """

    metrics: tuple[str, ...]
    events: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssessRules:
    weights: RootCauseWeights
    matching: FmeaMatchingRules
    match_scores: dict[str, float]
    action_priority: ActionPriorityRules
    fallback_risk: FallbackRisk
    historical_evidence_scale: float
    cause_evidence: dict[str, CauseEvidenceRules]
    failure_modes: dict[str, str]


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def load_settings() -> Settings:
    return Settings(
        data_provider=os.getenv("DATA_PROVIDER", "mock").strip().lower(),
        ai_provider=os.getenv("AI_PROVIDER", "mock").strip().lower(),
        sup_os_api_url=os.getenv("SUPOS_API_URL") or None,
        sup_os_api_token=os.getenv("SUPOS_API_TOKEN") or None,
        sup_os_timeout_seconds=float(os.getenv("SUPOS_TIMEOUT_SECONDS", "10")),
        sup_os_verify_tls=_env_flag("SUPOS_VERIFY_TLS", default=True),
        sup_os_uns_root_alias=os.getenv("SUPOS_UNS_ROOT_ALIAS") or None,
        sup_os_event_source=os.getenv("SUPOS_EVENT_SOURCE", "mqtt").strip().lower(),
        sup_os_mqtt_url=os.getenv("SUPOS_MQTT_URL") or None,
        sup_os_mqtt_topic=os.getenv("SUPOS_MQTT_TOPIC", "factorymind/uns/#").strip(),
        sup_os_mqtt_username=os.getenv("SUPOS_USERNAME") or None,
        sup_os_mqtt_password=os.getenv("SUPOS_PASSWORD") or None,
        ai_base_url=os.getenv("AI_BASE_URL") or None,
        ai_api_key=os.getenv("AI_API_KEY") or None,
        ai_model=os.getenv("AI_MODEL") or None,
        ai_timeout_seconds=float(
            os.getenv("AI_TIMEOUT_SECONDS", str(DEFAULT_AI_TIMEOUT_SECONDS))
        ),
        ai_max_retries=int(os.getenv("AI_MAX_RETRIES", str(DEFAULT_AI_MAX_RETRIES))),
        demo_seed=int(os.getenv("DEMO_SEED", str(DEFAULT_DEMO_SEED))),
    )


def load_anomaly_rules(path: Path | None = None) -> AnomalyRules:
    """Read the detection rules. A missing or malformed file fails loudly: a
    silently defaulted threshold would make every anomaly score untrustworthy."""
    rules_path = path or Path(
        os.getenv("ANOMALY_RULES_PATH") or DEFAULT_ANOMALY_RULES_PATH
    )
    raw = json.loads(rules_path.read_text(encoding="utf-8"))

    severity = raw["severity"]
    evidence = raw["evidence"]
    verification = raw["verification"]
    metrics = {
        key: MetricRule(
            direction=str(value["direction"]).upper(),
            magnitude_scale=float(value["magnitudeScale"]),
            label=str(value["label"]),
            unit=str(value["unit"]),
            recovery_tolerance=float(value["recoveryTolerance"]),
        )
        for key, value in raw["metrics"].items()
    }

    return AnomalyRules(
        z_score_threshold=float(raw["zScoreThreshold"]),
        min_episode_points=int(raw["minEpisodePoints"]),
        max_gap_points=int(raw["maxGapPoints"]),
        severity=SeverityWeights(
            z_scale=float(severity["zScale"]),
            z_weight=float(severity["zWeight"]),
            magnitude_weight=float(severity["magnitudeWeight"]),
        ),
        evidence=EvidenceRules(
            min_correlated_z=float(evidence["minCorrelatedZ"]),
            full_scale_z=float(evidence["fullScaleZ"]),
            max_correlated_metrics=int(evidence["maxCorrelatedMetrics"]),
            min_baseline_points=int(evidence["minBaselinePoints"]),
            event_lookback_minutes=int(evidence["eventLookbackMinutes"]),
            max_events=int(evidence["maxEvents"]),
        ),
        verification=VerificationRules(
            window_minutes=int(verification["windowMinutes"]),
            min_points=int(verification["minPoints"]),
        ),
        metrics=metrics,
    )


def load_assess_rules(path: Path | None = None) -> AssessRules:
    """Read the FMEA-matching and root-cause-scoring rules (spec §8, §14).

    Like the anomaly rules, a missing or malformed file fails loudly: a silently
    defaulted weight would make every ranking untrustworthy."""
    rules_path = path or Path(
        os.getenv("ASSESS_RULES_PATH") or DEFAULT_ASSESS_RULES_PATH
    )
    raw = json.loads(rules_path.read_text(encoding="utf-8"))

    weights = raw["rootCauseWeights"]
    matching = raw["fmeaMatching"]
    ap = raw["actionPriority"]
    fallback = raw["fallbackRisk"]

    return AssessRules(
        weights=RootCauseWeights(
            correlation=float(weights["correlation"]),
            temporal=float(weights["temporal"]),
            magnitude=float(weights["magnitude"]),
            fmea_relevance=float(weights["fmeaRelevance"]),
            historical_evidence=float(weights["historicalEvidence"]),
        ),
        matching=FmeaMatchingRules(
            semantic_threshold=float(matching["semanticThreshold"]),
            max_candidates=int(matching["maxCandidates"]),
        ),
        match_scores={
            str(key): float(value) for key, value in raw["matchScores"].items()
        },
        action_priority=ActionPriorityRules(
            high_severity=int(ap["highSeverity"]),
            high_rpn=int(ap["highRpn"]),
            medium_severity=int(ap["mediumSeverity"]),
            medium_rpn=int(ap["mediumRpn"]),
        ),
        fallback_risk=FallbackRisk(
            occurrence=int(fallback["occurrence"]),
            detection=int(fallback["detection"]),
        ),
        historical_evidence_scale=float(raw["historicalEvidenceScale"]),
        cause_evidence={
            str(key): CauseEvidenceRules(
                metrics=tuple(str(m) for m in value["metrics"]),
                events=tuple(str(e) for e in value["events"]),
            )
            for key, value in raw["causeEvidence"].items()
        },
        failure_modes={
            str(key): str(value) for key, value in raw["failureModes"].items()
        },
    )