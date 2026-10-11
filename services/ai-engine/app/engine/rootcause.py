"""Deterministic root-cause ranking (spec §14).

    RootCauseScore =
        0.30 * CorrelationScore
      + 0.25 * TemporalScore
      + 0.20 * MagnitudeScore
      + 0.15 * FMEARelevance
      + 0.10 * HistoricalEvidence

Every term is computed from evidence already on screen, so an operator can audit
the ranking by hand. The LLM never ranks — it explains the ranking this module
produced (spec §6.1, §7).

The 4M1E category is what makes two causes of the *same* failure mode rank
differently: a machine cause is corroborated by tool-error evidence, a material
cause is not. That mapping lives in `assess_rules.json` (`causeEvidence`), not in
code, so a domain expert can retune which evidence supports which category.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.config import AnomalyRules, AssessRules
from app.domain.models import (
    Anomaly,
    Evidence,
    EvidenceType,
    FmeaMatchResult,
    FmeaRecord,
    FourMOneECategory,
    RootCauseCandidate,
)
from app.engine.anomaly import EVENT_LABELS


@dataclass(slots=True)
class _Aggregate:
    """Accumulates the FMEA rows that share one cause string."""

    category: FourMOneECategory | None
    fmea_score: float


def rank_root_causes(
    *,
    anomaly: Anomaly,
    evidence: Sequence[Evidence],
    match: FmeaMatchResult,
    records: Sequence[FmeaRecord],
    rules: AssessRules,
    anomaly_rules: AnomalyRules,
) -> list[RootCauseCandidate]:
    """Rank the causes behind a matched failure mode, strongest first.

    Causes come only from matched FMEA rows — a `NO_MATCH` yields no candidates
    (spec §8.2). Rows sharing a cause are folded together: the same cause seen in
    several failure modes is *stronger* historical evidence, not a duplicate.
    """
    if not match.candidates:
        return []

    expected = _expected_labels(rules, anomaly_rules)
    history = _cause_history(records)

    aggregated: dict[str, _Aggregate] = {}
    for candidate in match.candidates:
        record = candidate.record
        aggregate = aggregated.setdefault(
            record.cause, _Aggregate(category=record.category, fmea_score=0.0)
        )
        aggregate.fmea_score = max(aggregate.fmea_score, candidate.score)
        if aggregate.category is None:
            aggregate.category = record.category

    magnitude = _clamp01(anomaly.severity)
    candidates: list[RootCauseCandidate] = []
    for cause, aggregate in aggregated.items():
        labels = (
            expected.get(aggregate.category, frozenset()) if aggregate.category else frozenset()
        )
        confidence = (
            rules.weights.correlation * _correlation(evidence, labels)
            + rules.weights.temporal * _temporal(evidence, labels)
            + rules.weights.magnitude * magnitude
            + rules.weights.fmea_relevance * aggregate.fmea_score
            + rules.weights.historical_evidence
            * min(1.0, history.get(cause, 1) / rules.historical_evidence_scale)
        )
        candidates.append(
            RootCauseCandidate(
                cause=cause,
                confidence=round(_clamp01(confidence), 4),
                evidence_ids=_supporting_evidence(evidence, labels),
                category=aggregate.category,
            )
        )

    candidates.sort(key=lambda item: (-item.confidence, item.cause))
    return candidates[: rules.matching.max_candidates]


# --------------------------------------------------------------------------- #
# Score terms
# --------------------------------------------------------------------------- #


def _correlation(evidence: Sequence[Evidence], labels: frozenset[str]) -> float:
    """Strongest co-moving metric that this category would predict.

    A metric's evidence relevance already encodes how far it moved on its own
    robust scale, so it is used as-is — no second scale is invented here.
    """
    matching = _matching(evidence, labels, EvidenceType.METRIC_SHIFT)
    return max((item.relevance for item in matching), default=0.0)


def _temporal(evidence: Sequence[Evidence], labels: frozenset[str]) -> float:
    """Closest event that this category would predict.

    Event evidence relevance is exponential proximity to the episode (see
    `app.engine.anomaly`), which is exactly the temporal term the formula wants.
    """
    matching = _matching(evidence, labels, EvidenceType.EVENT_CORRELATION)
    return max((item.relevance for item in matching), default=0.0)


def _matching(
    evidence: Sequence[Evidence], labels: frozenset[str], kind: EvidenceType
) -> list[Evidence]:
    return [item for item in evidence if item.type is kind and item.source in labels]


def _supporting_evidence(evidence: Sequence[Evidence], labels: frozenset[str]) -> list[str]:
    supporting = [
        item
        for item in evidence
        if item.source in labels
        and item.type in (EvidenceType.METRIC_SHIFT, EvidenceType.EVENT_CORRELATION)
    ]
    supporting.sort(key=lambda item: (-item.relevance, item.id))
    return [item.id for item in supporting]


def _expected_labels(
    rules: AssessRules, anomaly_rules: AnomalyRules
) -> dict[FourMOneECategory, frozenset[str]]:
    """Translate `causeEvidence` keys into the labels evidence carries.

    Config speaks in machine keys (`toolErrorRate`, `TOOL_ERROR`); evidence
    carries human labels (`工具故障率`, `工具报警`). This is the one place the two
    vocabularies meet.
    """
    metric_labels = {key: rule.label for key, rule in anomaly_rules.metrics.items()}
    event_labels = {event.value: label for event, label in EVENT_LABELS.items()}

    mapping: dict[FourMOneECategory, frozenset[str]] = {}
    for category, cause_rules in rules.cause_evidence.items():
        labels = {metric_labels[key] for key in cause_rules.metrics if key in metric_labels}
        labels |= {event_labels[key] for key in cause_rules.events if key in event_labels}
        mapping[FourMOneECategory(category)] = frozenset(labels)
    return mapping


def _cause_history(records: Sequence[FmeaRecord]) -> dict[str, int]:
    history: dict[str, int] = {}
    for record in records:
        history[record.cause] = history.get(record.cause, 0) + 1
    return history


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))