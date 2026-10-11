"""Action loop and verification surface (spec §16 Page 6, §25).

The store is deliberately in-process and in-memory. An action here is a session
artefact of the walkthrough — not plant record data (spec §31) — and the demo
runs a single engine process, so there is nothing to share. It starts empty:
every action on screen was raised through the loop being demonstrated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request

from app.config import AnomalyRules
from app.domain.models import (
    ActionCreateRequest,
    ActionItem,
    ActionItemStatus,
    ActionUpdateRequest,
    ActionWithContext,
    Anomaly,
    Verification,
    VerifyActionResponse,
)
from app.engine.verify import measure_recovery
from app.providers.base import IndustrialDataProvider

router = APIRouter(prefix="/api")


@dataclass(slots=True)
class _StoredAction:
    item: ActionItem
    anomaly: Anomaly
    verifications: list[Verification] = field(default_factory=list)


class ActionStore:
    """In-memory action registry. Every method runs to completion without an
    `await`, so a single-threaded event loop cannot interleave two writes."""

    def __init__(self) -> None:
        self._items: dict[str, _StoredAction] = {}
        self._sequence = 0

    def _next_id(self) -> str:
        self._sequence += 1
        return f"ACT-{self._sequence:04d}"

    def list(
        self,
        *,
        anomaly_id: str | None = None,
        status: ActionItemStatus | None = None,
    ) -> list[ActionWithContext]:
        entries = list(self._items.values())
        if anomaly_id:
            entries = [entry for entry in entries if entry.item.anomaly_id == anomaly_id]
        if status:
            entries = [entry for entry in entries if entry.item.status is status]
        return [
            ActionWithContext(
                action=entry.item,
                anomaly=entry.anomaly,
                latest_verification=entry.verifications[-1] if entry.verifications else None,
            )
            for entry in entries
        ]

    def get(self, action_id: str) -> _StoredAction:
        entry = self._items.get(action_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Unknown action: {action_id}")
        return entry

    def create(self, body: ActionCreateRequest) -> _StoredAction:
        item = ActionItem(
            id=self._next_id(),
            anomaly_id=body.anomaly.id,
            title=body.title,
            owner=body.owner,
            due_date=body.due_date,
            status=ActionItemStatus.OPEN,
            priority=body.priority,
        )
        entry = _StoredAction(item=item, anomaly=body.anomaly)
        self._items[item.id] = entry
        return entry

    def update(self, action_id: str, body: ActionUpdateRequest) -> _StoredAction:
        entry = self.get(action_id)
        changes = body.model_dump(exclude_none=True)
        if changes:
            entry.item = entry.item.model_copy(update=changes)
        return entry


def _store(request: Request) -> ActionStore:
    return request.app.state.action_store


def _provider(request: Request) -> IndustrialDataProvider:
    return request.app.state.data_provider


def _rules(request: Request) -> AnomalyRules:
    return request.app.state.anomaly_rules


@router.get("/actions", response_model=list[ActionWithContext])
async def list_actions(
    request: Request,
    anomaly_id: str | None = Query(default=None, alias="anomalyId"),
    status: ActionItemStatus | None = Query(default=None),
) -> list[ActionWithContext]:
    return _store(request).list(anomaly_id=anomaly_id, status=status)


@router.post("/actions", response_model=ActionItem, status_code=201)
async def create_action(request: Request, body: ActionCreateRequest) -> ActionItem:
    return _store(request).create(body).item


@router.patch("/actions/{action_id}", response_model=ActionItem)
async def update_action(
    request: Request, action_id: str, body: ActionUpdateRequest
) -> ActionItem:
    return _store(request).update(action_id, body).item


@router.post("/actions/{action_id}/verify", response_model=VerifyActionResponse)
async def verify_action(request: Request, action_id: str) -> VerifyActionResponse:
    """Re-measure the metric the action answers and record the verdict.

    The action snapshot supplies the station, metric and target; the data
    provider supplies the post-action level. Nothing the client sends can move
    the target or the measured value.
    """
    store = _store(request)
    entry = store.get(action_id)
    anomaly = entry.anomaly
    rules = _rules(request)
    metric_rule = rules.metrics[anomaly.metric.value]

    verified_at = datetime.now().replace(second=0, microsecond=0)
    points = await _provider(request).get_metrics(
        metric=anomaly.metric,
        start=verified_at - timedelta(minutes=rules.verification.window_minutes),
        end=verified_at,
        station_id=anomaly.station_id,
    )
    measurement = measure_recovery(
        points,
        metric_rule=metric_rule,
        target=anomaly.baseline,
        min_points=rules.verification.min_points,
    )
    if measurement is None:
        raise HTTPException(
            status_code=409,
            detail="恢复窗口内样本不足，无法完成验证，请稍后重试。",
        )

    verification = Verification(
        action_id=entry.item.id,
        verified_at=verified_at,
        metric=anomaly.metric,
        before=anomaly.actual,
        after=measurement.after,
        target=anomaly.baseline,
        passed=measurement.passed,
    )
    entry.verifications.append(verification)
    if measurement.passed and entry.item.status is not ActionItemStatus.DONE:
        entry.item = entry.item.model_copy(update={"status": ActionItemStatus.DONE})
    return VerifyActionResponse(action=entry.item, verification=verification)