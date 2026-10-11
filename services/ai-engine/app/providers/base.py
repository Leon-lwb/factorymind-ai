"""Data-source abstraction (spec §22).

`MockIndustrialDataProvider` and the future `SupOsProvider` implement the same
protocol, so swapping `DATA_PROVIDER` changes nothing above this layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.domain.models import (
    Equipment,
    Factory,
    FmeaRecord,
    IndustrialEvent,
    IndustrialEventType,
    Line,
    MetricKey,
    MetricPoint,
    Process,
    Station,
)


class IndustrialDataProvider(Protocol):
    async def get_factories(self) -> Sequence[Factory]: ...

    async def get_lines(self, factory_id: str) -> Sequence[Line]: ...

    async def get_stations(self, line_id: str) -> Sequence[Station]: ...

    async def get_equipment(self, station_id: str) -> Sequence[Equipment]: ...

    async def get_processes(self, station_id: str) -> Sequence[Process]: ...

    async def get_metrics(
        self,
        *,
        metric: MetricKey,
        start: datetime,
        end: datetime,
        station_id: str | None = None,
        line_id: str | None = None,
    ) -> Sequence[MetricPoint]: ...

    async def get_events(
        self,
        *,
        start: datetime,
        end: datetime,
        station_id: str | None = None,
        line_id: str | None = None,
        types: Sequence[IndustrialEventType] | None = None,
    ) -> Sequence[IndustrialEvent]: ...

    async def get_fmea_records(self) -> Sequence[FmeaRecord]: ...