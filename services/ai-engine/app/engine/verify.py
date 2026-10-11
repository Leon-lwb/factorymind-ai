"""Deterministic recovery measurement (spec §16 Page 6).

Verification is a *re-measurement*, not a restatement of the anomaly it answers.
The engine reads the metric again over the window that follows the action and
compares the robust level against the recovery band. The verdict is arithmetic
on the same series the detector used — the AI layer never touches it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from app.config import DIRECTION_DOWN, MetricRule
from app.domain.models import MetricPoint


@dataclass(frozen=True, slots=True)
class RecoveryMeasurement:
    after: float
    passed: bool


def robust_level(points: Sequence[MetricPoint]) -> float:
    """Median of the recovery window — the level an operator would read off the
    chart. Median, not mean, so one stray bucket cannot flip the verdict."""
    return round(median(point.value for point in points), 3)


def within_recovery_band(
    *, direction: str, after: float, target: float, tolerance: float
) -> bool:
    """`direction` is the metric's *bad* direction (spec §13). A metric whose bad
    direction is DOWN has recovered once it climbs back into the band; one whose
    bad direction is UP has recovered once it drops back into it."""
    if direction == DIRECTION_DOWN:
        return after >= target - tolerance
    return after <= target + tolerance


def measure_recovery(
    points: Sequence[MetricPoint],
    *,
    metric_rule: MetricRule,
    target: float,
    min_points: int,
) -> RecoveryMeasurement | None:
    """`None` when the window holds too few samples to read a level from — an
    absent measurement must never be reported as a passing one."""
    if len(points) < min_points:
        return None
    after = robust_level(points)
    return RecoveryMeasurement(
        after=after,
        passed=within_recovery_band(
            direction=metric_rule.direction,
            after=after,
            target=target,
            tolerance=metric_rule.recovery_tolerance,
        ),
    )