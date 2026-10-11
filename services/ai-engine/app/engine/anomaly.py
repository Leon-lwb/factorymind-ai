"""Deterministic anomaly detection (spec §13, §43).

Rule + statistics only — no trained model — so every finding is explainable and
reproducible from the raw series. The pipeline is:

1. split the window into one series per (station, metric);
2. estimate a robust baseline (median) and scale (MAD-derived sigma);
3. flag every point whose z-score crosses the threshold *in the metric's bad
   direction* — FPY falling is a fault, FPY rising is not;
4. merge consecutive flagged points into a single episode, so one incident
   yields one anomaly rather than one per bucket;
5. score severity and attach the evidence that explains the shift.

Median/MAD are used instead of mean/std because the window contains the very
excursion we are looking for: mean/std would be dragged by it, median/MAD stay
on the healthy baseline until more than half the window is faulty.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from app.config import DIRECTION_DOWN, DIRECTION_UP, AnomalyRules, MetricRule
from app.domain.models import (
    Anomaly,
    AnomalyDirection,
    AnomalyStatus,
    Evidence,
    EvidenceType,
    IndustrialEvent,
    IndustrialEventType,
    MetricKey,
    MetricPoint,
)

# Scales MAD to a consistent sigma estimate for normally distributed data.
MAD_SCALE = 1.4826

# Short Chinese labels for the evidence list, where the source column is narrow.
EVENT_LABELS: dict[IndustrialEventType, str] = {
    IndustrialEventType.MAINTENANCE: "保养事件",
    IndustrialEventType.TOOL_ERROR: "工具报警",
    IndustrialEventType.PLC_ERROR: "PLC 报警",
    IndustrialEventType.SENSOR_FAULT: "传感器故障",
    IndustrialEventType.MATERIAL_CHANGE: "物料变更",
    IndustrialEventType.SHIFT_CHANGE: "班次交接",
}


@dataclass(frozen=True, slots=True)
class _Stats:
    baseline: float
    sigma: float


def detect_anomalies(
    points: Sequence[MetricPoint],
    *,
    rules: AnomalyRules,
    factory_id: str,
    station_lines: Mapping[str, str],
    events: Sequence[IndustrialEvent] = (),
) -> list[Anomaly]:
    """Return the anomalies in `points`, most severe first."""
    grouped = _group(points)
    stats_by_key = {
        key: _robust_stats([point.value for point in series])
        for key, series in grouped.items()
    }

    anomalies: list[Anomaly] = []
    for (station_id, metric), series in grouped.items():
        rule = rules.metrics.get(metric.value)
        if rule is None:
            continue
        stats = stats_by_key[(station_id, metric)]
        for episode in _episodes(series, stats, rule, rules):
            anomaly = _build_anomaly(
                station_id=station_id,
                line_id=station_lines.get(station_id, ""),
                factory_id=factory_id,
                metric=metric,
                rule=rule,
                stats=stats,
                episode=episode,
                rules=rules,
            )
            anomaly.evidence = _correlate(anomaly, grouped, stats_by_key, rules, events)
            anomalies.append(anomaly)

    anomalies.sort(key=lambda item: (-item.severity, item.timestamp))
    return anomalies


# --------------------------------------------------------------------------- #
# Series statistics
# --------------------------------------------------------------------------- #


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _robust_stats(values: Sequence[float]) -> _Stats:
    """Median and MAD-derived sigma over the whole window.

    Known limitation: when the excursion is one-sided and occupies well under
    half the window, the estimate does not collapse but does drift. A third of
    the window sitting below the baseline slides the median down to the
    (50% − contamination) percentile of the healthy points and inflates MAD by
    ~70%. Measured on the demo series: a baseline of 99.49 against a true 99.60
    and sigma 0.28 against 0.16, so z-scores are understated by ~40%. The bias
    is conservative — it suppresses detections rather than inventing them — and
    it shrinks as the excursion occupies less of the window.

    Trimming the excursion away is deliberately *not* done here: the synthetic
    jitter is bounded, so at small sample sizes MAD understates sigma, the trim
    band clips the distribution's own tails, and the estimate shrinks further —
    which manufactures anomalies out of quiet data. A trimmed estimator becomes
    sound only once the noise is unbounded (e.g. Gaussian).
    """
    baseline = _median(values)
    mad = _median([abs(value - baseline) for value in values])
    return _Stats(baseline=baseline, sigma=MAD_SCALE * mad)


def _group(
    points: Sequence[MetricPoint],
) -> dict[tuple[str, MetricKey], list[MetricPoint]]:
    grouped: dict[tuple[str, MetricKey], list[MetricPoint]] = {}
    for point in points:
        grouped.setdefault((point.station_id, point.metric), []).append(point)
    for series in grouped.values():
        series.sort(key=lambda item: item.timestamp)
    return grouped


# --------------------------------------------------------------------------- #
# Episode detection
# --------------------------------------------------------------------------- #


def _is_excursion(value: float, stats: _Stats, rule: MetricRule, threshold: float) -> bool:
    if stats.sigma <= 0.0:
        return False
    z = (value - stats.baseline) / stats.sigma
    if rule.direction == DIRECTION_DOWN:
        return z <= -threshold
    return z >= threshold


def _episodes(
    series: Sequence[MetricPoint],
    stats: _Stats,
    rule: MetricRule,
    rules: AnomalyRules,
) -> list[list[MetricPoint]]:
    """Group flagged points into episodes, bridging short gaps.

    A single fault makes the z-score hover around the threshold, so noise alone
    would split it into a handful of fragments. Bridging gaps of up to
    `max_gap_points` keeps one incident as one anomaly, while `min_episode_points`
    still discards lone blips.
    """
    episodes: list[list[MetricPoint]] = []
    current: list[MetricPoint] = []
    hits = 0
    gap = 0

    for point in series:
        if _is_excursion(point.value, stats, rule, rules.z_score_threshold):
            current.append(point)
            hits += 1
            gap = 0
        elif current:
            gap += 1
            if gap > rules.max_gap_points:
                if hits >= rules.min_episode_points:
                    episodes.append(current)
                current = []
                hits = 0
                gap = 0
            else:
                current.append(point)

    if current and hits >= rules.min_episode_points:
        episodes.append(current)
    return episodes


def _severity(z_peak: float, delta: float, rule: MetricRule, rules: AnomalyRules) -> float:
    """0–1 score blending how far the excursion went with how large it was.

    Both components saturate, so a runaway z-score cannot mask a trivial
    magnitude change and vice versa.
    """
    z_component = min(1.0, z_peak / rules.severity.z_scale)
    magnitude_component = min(1.0, abs(delta) / rule.magnitude_scale)
    score = (
        rules.severity.z_weight * z_component
        + rules.severity.magnitude_weight * magnitude_component
    )
    return round(min(1.0, max(0.0, score)), 4)


def _build_anomaly(
    *,
    station_id: str,
    line_id: str,
    factory_id: str,
    metric: MetricKey,
    rule: MetricRule,
    stats: _Stats,
    episode: Sequence[MetricPoint],
    rules: AnomalyRules,
) -> Anomaly:
    # `actual` is the peak excursion of the episode: the reading the operator
    # would have seen on the worst bucket. `z_score` is that same peak's z.
    peak = max(episode, key=lambda point: abs(point.value - stats.baseline))
    actual = peak.value
    delta = actual - stats.baseline
    ratio = delta / stats.baseline if stats.baseline else 0.0
    z_peak = abs(delta) / stats.sigma if stats.sigma > 0.0 else 0.0

    episode_start = episode[0].timestamp
    episode_end = episode[-1].timestamp

    return Anomaly(
        id=f"AN-{station_id}-{metric.value}-{episode_start.strftime('%Y%m%dT%H%M')}",
        timestamp=episode_start,
        factory_id=factory_id,
        line_id=line_id,
        station_id=station_id,
        metric=metric,
        baseline=round(stats.baseline, 3),
        actual=round(actual, 3),
        delta=round(delta, 3),
        delta_ratio=round(ratio, 4),
        direction=AnomalyDirection.DOWN if delta < 0 else AnomalyDirection.UP,
        z_score=round(z_peak, 2),
        severity=_severity(z_peak, delta, rule, rules),
        status=AnomalyStatus.NEW,
        window_start=episode_start,
        window_end=episode_end,
    )


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


def _shift_text(rule: MetricRule, baseline: float, actual: float) -> str:
    delta = actual - baseline
    ratio = (delta / baseline * 100.0) if baseline else 0.0
    arrow = "↑" if delta >= 0 else "↓"
    return f"{baseline:.2f}{rule.unit} → {actual:.2f}{rule.unit} ({arrow} {abs(ratio):.1f}%)"


def _correlate(
    anomaly: Anomaly,
    grouped: Mapping[tuple[str, MetricKey], Sequence[MetricPoint]],
    stats_by_key: Mapping[tuple[str, MetricKey], _Stats],
    rules: AnomalyRules,
    events: Sequence[IndustrialEvent],
) -> list[Evidence]:
    """Explain an anomaly with its own shift, its co-moving metrics, and the
    events that sit next to it in time.

    Correlation here is co-movement inside the same episode window and temporal
    proximity — it is evidence, not proof, and is reported with a relevance
    score rather than a verdict (spec §7).
    """
    primary_rule = rules.metrics[anomaly.metric.value]
    evidence = [
        Evidence(
            id=f"EV-{anomaly.id}-00",
            type=EvidenceType.METRIC_SHIFT,
            source=primary_rule.label,
            value=_shift_text(primary_rule, anomaly.baseline, anomaly.actual),
            relevance=1.0,
            timestamp=anomaly.timestamp,
        )
    ]

    correlated: list[tuple[float, MetricRule, float, float]] = []
    for metric in MetricKey:
        if metric is anomaly.metric:
            continue
        rule = rules.metrics.get(metric.value)
        series = grouped.get((anomaly.station_id, metric))
        if rule is None or not series:
            continue
        in_episode = [
            point.value
            for point in series
            if anomaly.window_start <= point.timestamp <= anomaly.window_end
        ]
        if not in_episode:
            continue
        # Estimate the co-metric's healthy level from the points *outside* the
        # episode. The window is where the fault lives, so folding it into the
        # baseline drags the reference toward the fault and inflates sigma —
        # the contamination that merely makes the primary test conservative
        # would here silence a genuine co-movement outright.
        outside = [
            point.value
            for point in series
            if not anomaly.window_start <= point.timestamp <= anomaly.window_end
        ]
        if len(outside) >= rules.evidence.min_baseline_points:
            stats = _robust_stats(outside)
        else:
            stats = stats_by_key[(anomaly.station_id, metric)]
        if stats.sigma <= 0.0:
            continue
        actual = _median(in_episode)
        # Judge the co-movement on that metric's own robust scale, and only in
        # its bad direction: a metric that improved is not evidence of a fault.
        z = (actual - stats.baseline) / stats.sigma
        bad_z = z if rule.direction == DIRECTION_UP else -z
        if bad_z < rules.evidence.min_correlated_z:
            continue
        relevance = min(1.0, bad_z / rules.evidence.full_scale_z)
        correlated.append((relevance, rule, stats.baseline, actual))

    correlated.sort(key=lambda item: item[0], reverse=True)
    for index, (relevance, rule, baseline, actual) in enumerate(
        correlated[: rules.evidence.max_correlated_metrics], start=1
    ):
        evidence.append(
            Evidence(
                id=f"EV-{anomaly.id}-{index:02d}",
                type=EvidenceType.METRIC_SHIFT,
                source=rule.label,
                value=_shift_text(rule, baseline, actual),
                relevance=round(relevance, 4),
                timestamp=anomaly.timestamp,
            )
        )

    evidence.extend(_event_evidence(anomaly, events, rules))
    return evidence


def _event_evidence(
    anomaly: Anomaly,
    events: Sequence[IndustrialEvent],
    rules: AnomalyRules,
) -> list[Evidence]:
    """Attach the events that sit next to the episode in time.

    Spec §10.3 wants the fault to carry a temporal story — a maintenance action,
    then a tool error, then the metric shift — so the operator sees *when* things
    went wrong, not only *how much*. Relevance is temporal proximity to the
    episode start; a precursor event is allowed to precede it, which is what
    makes the chain readable as a cause rather than a coincidence.

    Proximity decays exponentially over the lookback rather than linearly. A
    linear ramp drives an event at the lookback boundary to exactly zero, which
    reads as "worthless evidence" — yet the maintenance action that *explains*
    the fault is precisely the oldest link, sitting at the far end of the
    lookback. Exponential decay keeps the ordering (nearer is stronger) while
    never claiming a precursor is worthless.

    These are returned oldest first, not by relevance: the chain is a narrative,
    and reordering it by score would destroy the sequence it exists to convey.
    """
    lookback = rules.evidence.event_lookback_minutes
    earliest = anomaly.window_start - timedelta(minutes=lookback)

    near: list[tuple[float, IndustrialEvent]] = []
    for event in events:
        if event.station_id != anomaly.station_id:
            continue
        if not earliest <= event.timestamp <= anomaly.window_end:
            continue
        # Events inside the window are contemporaneous (distance 0); earlier
        # ones decay with how far they precede it.
        gap_minutes = max(
            0.0, (anomaly.window_start - event.timestamp).total_seconds() / 60.0
        )
        near.append((math.exp(-gap_minutes / lookback), event))

    near.sort(key=lambda item: (item[1].timestamp, item[1].event_id))
    return [
        Evidence(
            id=f"EV-{anomaly.id}-E{index:02d}",
            type=EvidenceType.EVENT_CORRELATION,
            source=EVENT_LABELS[event.type],
            value=event.message,
            relevance=round(relevance, 4),
            timestamp=event.timestamp,
        )
        for index, (relevance, event) in enumerate(near[: rules.evidence.max_events], start=1)
    ]