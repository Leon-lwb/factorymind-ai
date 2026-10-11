"""AI explanation layer (spec §6.1, §7, §15, §20).

The deterministic engine owns every number — FPY, z-scores, RPN, AP and the
root-cause ranking. The AI provider only *explains*: it turns the computed facts
into a summary and recommendations, and every statement it makes is traceable to
evidence already present. It receives the ranked causes and risk as input and
must not recompute or override them.

`MockAiProvider` emits exactly the JSON shape a real model would, so the demo
never depends on a live API (spec §21) and the contract is proven before any
real provider is wired in.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.models import (
    ActionPriority,
    AiAnalysisResult,
    Anomaly,
    AnomalyDirection,
    Equipment,
    Evidence,
    FmeaMatchResult,
    FmeaMatchStatus,
    Process,
    RecommendedAction,
    RiskAssessment,
    RiskLevel,
    RootCauseCandidate,
)


@dataclass(frozen=True, slots=True)
class AiAnalysisRequest:
    """Everything the explanation layer is allowed to look at.

    The provider receives *already computed* root causes and risk: it may phrase
    them, never recompute them (spec §6.1).
    """

    anomaly: Anomaly
    evidence: Sequence[Evidence]
    process: Process | None
    equipment: Equipment | None
    root_causes: Sequence[RootCauseCandidate]
    risk: RiskAssessment
    fmea_match: FmeaMatchResult
    metric_label: str


class AiProvider(Protocol):
    name: str

    async def analyze(self, request: AiAnalysisRequest) -> AiAnalysisResult: ...


class AiProviderError(RuntimeError):
    """The explanation layer could not produce a trustworthy result.

    Raised for transport failures, malformed model output, and recommendations
    citing evidence that does not exist. It is never swallowed into a degraded
    answer: an unexplained anomaly is better than a confidently wrong one, and
    silently falling back to the mock would make a real deployment
    indistinguishable from the demo (spec §7, §23).
    """


AP_TO_RISK: dict[ActionPriority, RiskLevel] = {
    ActionPriority.HIGH: RiskLevel.HIGH,
    ActionPriority.MEDIUM: RiskLevel.MEDIUM,
    ActionPriority.LOW: RiskLevel.LOW,
}


class MockAiProvider:
    """Deterministic stand-in for a language model (spec §21).

    It performs no inference — it renders the deterministic result into the
    structure a real provider must return. That is deliberate: demo correctness
    must not depend on a model being reachable, and the output schema is fixed
    here so the real provider can be swapped in without touching the frontend.
    """

    name = "mock"

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or datetime.now

    async def analyze(self, request: AiAnalysisRequest) -> AiAnalysisResult:
        return AiAnalysisResult(
            summary=self._summary(request),
            root_cause_candidates=list(request.root_causes),
            risk=request.risk,
            recommended_actions=self._recommended_actions(request),
            provider=self.name,
            generated_at=self._clock(),
        )

    def _summary(self, request: AiAnalysisRequest) -> str:
        anomaly = request.anomaly
        motion = "下降" if anomaly.direction is AnomalyDirection.DOWN else "上升"
        head = (
            f"{request.metric_label} 在 {anomaly.station_id} 出现异常{motion}："
            f"基线 {anomaly.baseline:g} → 实测 {anomaly.actual:g}"
            f"（z={anomaly.z_score:g}σ，幅度 {abs(anomaly.delta_ratio) * 100:.1f}%）。"
        )
        if request.root_causes:
            top = request.root_causes[0]
            body = f"按证据加权，最可能的根因为「{top.cause}」（置信度 {top.confidence:.0%}）。"
        elif request.fmea_match.match_status is FmeaMatchStatus.NO_MATCH:
            body = "未在 Master FMEA 中找到匹配记录，需人工确认根因并补充 FMEA。"
        else:
            body = "现有证据不足以给出根因候选。"
        return f"{head}{body}以上为基于证据的推断，非确定性结论，请结合现场确认。"

    def _recommended_actions(self, request: AiAnalysisRequest) -> list[RecommendedAction]:
        priority = AP_TO_RISK[request.risk.ap]
        action_by_cause: dict[str, str] = {}
        for candidate in request.fmea_match.candidates:
            action = candidate.record.recommended_action
            if action:
                action_by_cause.setdefault(candidate.record.cause, action)

        actions: list[RecommendedAction] = []
        seen: set[str] = set()
        # Ranked causes lead, so the strongest explanation heads the action list
        # rather than whatever order the FMEA file happens to be in.
        ordered_causes = [item.cause for item in request.root_causes]
        ordered_causes += [cause for cause in action_by_cause if cause not in ordered_causes]
        for cause in ordered_causes:
            action = action_by_cause.get(cause)
            if not action or action in seen:
                continue
            seen.add(action)
            actions.append(
                RecommendedAction(
                    action=action,
                    priority=priority,
                    rationale=f"来自 FMEA「{cause}」的既定措施",
                )
            )
        if not actions:
            actions.append(
                RecommendedAction(
                    action="人工确认异常根因，补充或更新 Master FMEA 记录",
                    priority=priority,
                    rationale="无匹配 FMEA 记录，无法引用既定措施（spec §8.2）",
                )
            )
        return actions