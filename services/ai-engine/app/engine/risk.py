"""Deterministic risk scoring (spec §6.1, §12, §15).

RPN and AP are arithmetic on S/O/D — never an LLM's opinion. A triage decision
that rests on these numbers must be reproducible from the FMEA row alone, so an
auditor can recompute it by hand and get the same answer.
"""

from __future__ import annotations

from app.config import ActionPriorityRules, AssessRules
from app.domain.models import (
    ActionPriority,
    Anomaly,
    FmeaMatchResult,
    RiskAssessment,
)

MIN_SCORE = 1
MAX_SCORE = 10


def clamp_score(value: float) -> int:
    """Clamp a raw rating onto the 1–10 FMEA scale."""
    return max(MIN_SCORE, min(MAX_SCORE, int(round(value))))


def compute_rpn(severity: int, occurrence: int, detection: int) -> int:
    return severity * occurrence * detection


def compute_action_priority(
    severity: int, occurrence: int, detection: int, rules: ActionPriorityRules
) -> ActionPriority:
    """Compact AP rule — the full AIAG-VDA table is out of scope (spec §12).

    Severity dominates: a safety-relevant failure is never downgraded by a low
    RPN. That is the one property of the real table worth keeping, and it is
    what stops a rare-but-severe defect from sinking to the bottom of the list.
    """
    rpn = compute_rpn(severity, occurrence, detection)
    if severity >= rules.high_severity or rpn >= rules.high_rpn:
        return ActionPriority.HIGH
    if severity >= rules.medium_severity or rpn >= rules.medium_rpn:
        return ActionPriority.MEDIUM
    return ActionPriority.LOW


def assess_risk(
    *, severity: float, occurrence: float, detection: float, rules: ActionPriorityRules
) -> RiskAssessment:
    s = clamp_score(severity)
    o = clamp_score(occurrence)
    d = clamp_score(detection)
    return RiskAssessment(
        severity=s,
        occurrence=o,
        detection=d,
        rpn=compute_rpn(s, o, d),
        ap=compute_action_priority(s, o, d, rules),
    )


def severity_from_anomaly(anomaly_severity: float) -> int:
    """Map the engine's 0–1 severity onto the 1–10 FMEA scale.

    Used only when no FMEA row matched: the excursion still needs a severity to
    score against, and deriving it from the observed shift keeps the fallback
    tied to evidence rather than to a guess.
    """
    return clamp_score(1.0 + 9.0 * max(0.0, min(1.0, anomaly_severity)))


def risk_from_match(
    match: FmeaMatchResult, anomaly: Anomaly, rules: AssessRules
) -> RiskAssessment:
    """Risk of the best-matching FMEA row, or a flagged fallback when none matched.

    The fallback still yields a presentable assessment, but it is built from the
    anomaly's own severity plus conservative O/D defaults — and it is flagged for
    human review upstream (spec §8.2), never presented as an established rating.
    """
    if match.candidates:
        record = match.candidates[0].record
        return assess_risk(
            severity=record.severity,
            occurrence=record.occurrence,
            detection=record.detection,
            rules=rules.action_priority,
        )
    return assess_risk(
        severity=severity_from_anomaly(anomaly.severity),
        occurrence=rules.fallback_risk.occurrence,
        detection=rules.fallback_risk.detection,
        rules=rules.action_priority,
    )