"""HTTP routes for the industrial data surface.

Every handler is a thin adapter over `IndustrialDataProvider`; all analytics
live in the engine modules so the same logic is reachable from tests and the
benchmark harness.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from app import __version__
from app.domain.models import (
    Equipment,
    Factory,
    IndustrialEvent,
    IndustrialEventType,
    Line,
    MetricKey,
    MetricPoint,
    Process,
    Station,
)
from app.providers.base import IndustrialDataProvider
from app.routers.common import resolve_window

router = APIRouter(prefix="/api")


def _provider(request: Request) -> IndustrialDataProvider:
    return request.app.state.data_provider


def _settings(request: Request):
    return request.app.state.settings


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    settings = _settings(request)
    return {
        "status": "ok",
        "dataProvider": settings.data_provider,
        "aiProvider": settings.ai_provider,
        "version": __version__,
    }


@router.get("/factories", response_model=list[Factory])
async def factories(request: Request) -> list[Factory]:
    return list(await _provider(request).get_factories())


@router.get("/lines", response_model=list[Line])
async def lines(request: Request, factory_id: str = Query(alias="factoryId")) -> list[Line]:
    return list(await _provider(request).get_lines(factory_id))


@router.get("/stations", response_model=list[Station])
async def stations(request: Request, line_id: str = Query(alias="lineId")) -> list[Station]:
    return list(await _provider(request).get_stations(line_id))


@router.get("/equipment", response_model=list[Equipment])
async def equipment(
    request: Request, station_id: str = Query(alias="stationId")
) -> list[Equipment]:
    return list(await _provider(request).get_equipment(station_id))


@router.get("/processes", response_model=list[Process])
async def processes(
    request: Request, station_id: str = Query(alias="stationId")
) -> list[Process]:
    return list(await _provider(request).get_processes(station_id))


@router.get("/metrics", response_model=list[MetricPoint])
async def metrics(
    request: Request,
    metric: MetricKey,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    station_id: str | None = Query(default=None, alias="stationId"),
    line_id: str | None = Query(default=None, alias="lineId"),
) -> list[MetricPoint]:
    start, end = resolve_window(start, end)

    points = await _provider(request).get_metrics(
        metric=metric,
        start=start,
        end=end,
        station_id=station_id,
        line_id=line_id,
    )
    return list(points)


@router.get("/events", response_model=list[IndustrialEvent])
async def events(
    request: Request,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    station_id: str | None = Query(default=None, alias="stationId"),
    line_id: str | None = Query(default=None, alias="lineId"),
    types: str | None = Query(default=None, description="Comma-separated event types"),
) -> list[IndustrialEvent]:
    start, end = resolve_window(start, end)

    parsed_types: list[IndustrialEventType] | None = None
    if types:
        try:
            parsed_types = [
                IndustrialEventType(value.strip()) for value in types.split(",") if value.strip()
            ]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown event type: {exc}") from exc

    result = await _provider(request).get_events(
        start=start,
        end=end,
        station_id=station_id,
        line_id=line_id,
        types=parsed_types,
    )
    return list(result)