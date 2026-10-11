"""Anomaly detection surface (spec §13, §16 Page 3, §43).

The route only gathers a window of metrics and hands them to
`app.engine.anomaly`; all detection logic lives in the engine so the same code
is reachable from tests and the benchmark harness.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request

from app.config import AnomalyRules
from app.domain.models import Anomaly, IndustrialEvent, MetricKey, MetricPoint
from app.engine import detect_anomalies
from app.providers.base import IndustrialDataProvider
from app.routers.common import resolve_window

router = APIRouter(prefix="/api")


def _provider(request: Request) -> IndustrialDataProvider:
    return request.app.state.data_provider


def _rules(request: Request) -> AnomalyRules:
    return request.app.state.anomaly_rules


async def _station_lines(
    provider: IndustrialDataProvider,
    *,
    station_id: str | None,
    line_id: str | None,
) -> tuple[str, dict[str, str]]:
    """Map every in-scope station to its line, and return the factory id."""
    factories = await provider.get_factories()
    if not factories:
        return "", {}

    factory_id = factories[0].factory_id
    mapping: dict[str, str] = {}
    for line in await provider.get_lines(factory_id):
        if line_id and line.line_id != line_id:
            continue
        for station in await provider.get_stations(line.line_id):
            if station_id and station.station_id != station_id:
                continue
            mapping[station.station_id] = line.line_id
    return factory_id, mapping


@router.get("/anomalies", response_model=list[Anomaly])
async def anomalies(
    request: Request,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    station_id: str | None = Query(default=None, alias="stationId"),
    line_id: str | None = Query(default=None, alias="lineId"),
) -> list[Anomaly]:
    window_start, window_end = resolve_window(start, end)
    provider = _provider(request)
    rules = _rules(request)
    factory_id, station_lines = await _station_lines(
        provider, station_id=station_id, line_id=line_id
    )
    if not station_lines:
        return []

    points: list[MetricPoint] = []
    for metric in MetricKey:
        points.extend(
            await provider.get_metrics(
                metric=metric,
                start=window_start,
                end=window_end,
                station_id=station_id,
                line_id=line_id,
            )
        )

    # The engine also accepts events just before the window: the maintenance
    # action that explains a fault happens *before* the metric starts to drift.
    events: list[IndustrialEvent] = list(
        await provider.get_events(
            start=window_start - timedelta(minutes=rules.evidence.event_lookback_minutes),
            end=window_end,
            station_id=station_id,
            line_id=line_id,
        )
    )

    return detect_anomalies(
        points,
        rules=rules,
        factory_id=factory_id,
        station_lines=station_lines,
        events=events,
    )