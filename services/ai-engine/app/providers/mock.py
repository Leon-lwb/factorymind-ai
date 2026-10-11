"""Reproducible synthetic data source (spec §10, §31).

All values are synthetic. No plant, vehicle, supplier or personnel data is
represented here, and none may be added — see spec §31 (data security).

Reproducibility: every generated value is a pure function of
(station, metric, timestamp bucket), hashed with SHA-256. Results therefore do
not depend on call order, process restarts, or the `--seed` flag alone, which
keeps benchmark runs and demo recordings identical.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.models import (
    METRIC_UNITS,
    Equipment,
    EquipmentType,
    Factory,
    FmeaRecord,
    IndustrialEvent,
    IndustrialEventType,
    Line,
    MetricKey,
    MetricPoint,
    Process,
    RiskLevel,
    Station,
)

BUCKET_MINUTES = 5
BUCKET = timedelta(minutes=BUCKET_MINUTES)
MAX_POINTS_PER_QUERY = 5_000

_FACTORIES: tuple[Factory, ...] = (
    Factory(factory_id="FAC-DEMO-01", name="示范总装工厂", country="CN"),
)

_LINES: tuple[Line, ...] = (
    Line(line_id="LINE-A", factory_id="FAC-DEMO-01", name="总装一线"),
    Line(line_id="LINE-B", factory_id="FAC-DEMO-01", name="总装二线"),
)

_STATIONS: tuple[Station, ...] = (
    Station(station_id="ST-A10", line_id="LINE-A", name="内饰装配"),
    Station(station_id="ST-A20", line_id="LINE-A", name="底盘合装"),
    Station(station_id="ST-A30", line_id="LINE-A", name="车门装配"),
    Station(station_id="ST-A40", line_id="LINE-A", name="关键扭矩拧紧"),
    Station(station_id="ST-A50", line_id="LINE-A", name="终检"),
    Station(station_id="ST-B10", line_id="LINE-B", name="内饰装配"),
    Station(station_id="ST-B20", line_id="LINE-B", name="底盘合装"),
    Station(station_id="ST-B30", line_id="LINE-B", name="关键扭矩拧紧"),
    Station(station_id="ST-B40", line_id="LINE-B", name="终检"),
)

_EQUIPMENT: tuple[Equipment, ...] = (
    Equipment(
        equipment_id="EQ-A40-01",
        station_id="ST-A40",
        type=EquipmentType.TORQUE_TOOL,
        name="电动拧紧枪 #1",
    ),
    Equipment(
        equipment_id="EQ-A40-02",
        station_id="ST-A40",
        type=EquipmentType.TORQUE_TOOL,
        name="电动拧紧枪 #2",
    ),
    Equipment(
        equipment_id="EQ-A40-03",
        station_id="ST-A40",
        type=EquipmentType.PLC,
        name="工位 PLC",
    ),
    Equipment(
        equipment_id="EQ-A20-01",
        station_id="ST-A20",
        type=EquipmentType.ROBOT,
        name="合装机器人",
    ),
)

_PROCESSES: tuple[Process, ...] = (
    Process(
        process_id="PR-A40-01",
        station_id="ST-A40",
        name="关键扭矩拧紧",
        operation="拧紧",
    ),
    Process(
        process_id="PR-A40-02",
        station_id="ST-A40",
        name="扭矩复检",
        operation="复检",
    ),
    Process(
        process_id="PR-A20-01",
        station_id="ST-A20",
        name="底盘合装定位",
        operation="合装",
    ),
)

# Baseline operating point per metric. Synthetic reference values.
_DEFAULT_BASELINE: dict[MetricKey, float] = {
    MetricKey.FPY: 99.6,
    MetricKey.NOK_RATE: 0.4,
    MetricKey.TORQUE_NOK_RATE: 0.3,
    MetricKey.CYCLE_TIME: 42.0,
    MetricKey.TOOL_ERROR_RATE: 0.2,
}

_STATION_OVERRIDES: dict[str, dict[MetricKey, float]] = {
    "ST-A20": {MetricKey.CYCLE_TIME: 56.0},
    "ST-A40": {MetricKey.CYCLE_TIME: 48.0, MetricKey.TORQUE_NOK_RATE: 0.35},
    "ST-A50": {MetricKey.CYCLE_TIME: 64.0},
    "ST-B30": {MetricKey.CYCLE_TIME: 47.0},
}

# Half-width of the uniform jitter, per metric. Each point is a 5-minute
# aggregate of many vehicles, so the residual is small. For uniform jitter,
# MAD = amplitude / 2, so the engine sees sigma ≈ 0.74 * amplitude. FPY, torque
# and cycle time are set so their incidents clear the 3-sigma detection gate with
# margin; tool error is set lower on purpose — see `_INCIDENTS`.
_NOISE_AMPLITUDE: dict[MetricKey, float] = {
    MetricKey.FPY: 0.22,
    MetricKey.NOK_RATE: 0.07,
    MetricKey.TORQUE_NOK_RATE: 0.03,
    MetricKey.CYCLE_TIME: 0.7,
    MetricKey.TOOL_ERROR_RATE: 0.024,
}

# --------------------------------------------------------------------------- #
# Engineered incident timeline (spec §10.3, §28)
# --------------------------------------------------------------------------- #
#
# The demo needs a *known* fault to detect, but every value must stay a pure
# function of the timestamp so recordings and benchmark runs reproduce exactly.
# A recurring cycle satisfies both: the incident repeats every `INCIDENT_CYCLE`,
# occupying its tail. Because the cycle equals the default query window, an
# 8-hour window always contains exactly one incident — either the ongoing one or
# the previous cycle's — and never two. That keeps the robust baseline below
# 50% contaminated, which is the range median/MAD can absorb.
#
# Wall-clock placement is naive-UTC so the schedule does not shift with the
# machine's timezone: 05:30–08:00, 13:30–16:00 and 21:30–24:00.

INCIDENT_CYCLE = timedelta(hours=8)
INCIDENT_ONSET = timedelta(hours=5, minutes=30)
INCIDENT_DURATION = timedelta(hours=2, minutes=30)
INCIDENT_RAMP = 0.2


@dataclass(frozen=True, slots=True)
class _Incident:
    """A step change applied to one metric on one station while active."""

    station_id: str
    metric: MetricKey
    peak_delta: float


_INCIDENTS: tuple[_Incident, ...] = (
    # ST-A40 "关键扭矩拧紧": the torque tool degrades, dragging FPY down and
    # stretching cycle time. The three shifts below are standalone anomalies;
    # together they read like the spec §16 Page-4 example (FPY ↓2.2%, torque ↑,
    # cycle ↑).
    _Incident(station_id="ST-A40", metric=MetricKey.FPY, peak_delta=-2.2),
    _Incident(station_id="ST-A40", metric=MetricKey.TORQUE_NOK_RATE, peak_delta=0.12),
    _Incident(station_id="ST-A40", metric=MetricKey.CYCLE_TIME, peak_delta=3.5),
    # Tool error is the *corroborating* signal, not a fault of its own: the spec
    # §16 Page-4 evidence list wants "Tool error ↑" under the FPY anomaly, but a
    # tool-error excursion is a symptom of the torque fault, not a separate
    # incident to triage. Its shift is therefore tuned to clear the 2-sigma
    # evidence gate while staying under the 3-sigma detection gate — it shows up
    # in the evidence chain and stays out of the anomaly list.
    _Incident(station_id="ST-A40", metric=MetricKey.TOOL_ERROR_RATE, peak_delta=0.045),
    # A weaker, isolated incident on the second line so the anomaly list has more
    # than one station to rank.
    _Incident(station_id="ST-B30", metric=MetricKey.CYCLE_TIME, peak_delta=2.6),
)


def _index_incidents() -> dict[tuple[str, MetricKey], tuple[_Incident, ...]]:
    index: dict[tuple[str, MetricKey], list[_Incident]] = {}
    for incident in _INCIDENTS:
        index.setdefault((incident.station_id, incident.metric), []).append(incident)
    return {key: tuple(value) for key, value in index.items()}


_INCIDENTS_BY_KEY = _index_incidents()


def _epoch(moment: datetime) -> int:
    """Seconds since epoch. Naive timestamps are read as UTC so the incident
    schedule never shifts with the machine's timezone."""
    return int(moment.replace(tzinfo=UTC).timestamp())


def _incident_onsets(start: datetime, end: datetime) -> tuple[datetime, ...]:
    """Every incident onset inside [start, end], oldest first."""
    cycle = int(INCIDENT_CYCLE.total_seconds())
    onset = int(INCIDENT_ONSET.total_seconds())
    start_epoch = _epoch(start)
    first = start_epoch + (cycle - (start_epoch - onset) % cycle) % cycle
    onsets: list[datetime] = []
    current = first
    while current <= _epoch(end):
        onsets.append(datetime.fromtimestamp(current, tz=UTC).replace(tzinfo=None))
        current += cycle
    return tuple(onsets)


def _incident_factor(moment: datetime) -> float:
    """Trapezoidal 0→1→0 envelope of the incident cycle at `moment`."""
    cycle = int(INCIDENT_CYCLE.total_seconds())
    position = _epoch(moment) % cycle
    onset = int(INCIDENT_ONSET.total_seconds())
    duration = int(INCIDENT_DURATION.total_seconds())
    if position < onset or position >= onset + duration:
        return 0.0
    progress = (position - onset) / duration
    if progress < INCIDENT_RAMP:
        return progress / INCIDENT_RAMP
    if progress > 1.0 - INCIDENT_RAMP:
        return (1.0 - progress) / INCIDENT_RAMP
    return 1.0


# --------------------------------------------------------------------------- #
# Engineered event timeline (spec §10.3)
# --------------------------------------------------------------------------- #
#
# Spec §10.3 requires the fault to carry a *temporal* story, not only a
# statistical one: a maintenance action precedes the tool error, which precedes
# the metric degradation. Anchoring the chain to the same cycle as the metric
# incidents keeps the two timelines from drifting apart, and deriving it from
# the timestamp keeps it reproducible.


@dataclass(frozen=True, slots=True)
class _EventTemplate:
    """One event at a fixed offset from the incident onset."""

    offset_minutes: int
    type: IndustrialEventType
    severity: RiskLevel
    equipment_id: str | None
    message: str


_EVENT_CHAINS: dict[str, tuple[_EventTemplate, ...]] = {
    "ST-A40": (
        _EventTemplate(
            offset_minutes=-5,
            type=IndustrialEventType.MAINTENANCE,
            severity=RiskLevel.MEDIUM,
            equipment_id="EQ-A40-01",
            message="电动拧紧枪 #1 预防性保养完成，标定值待复核",
        ),
        _EventTemplate(
            offset_minutes=0,
            type=IndustrialEventType.TOOL_ERROR,
            severity=RiskLevel.HIGH,
            equipment_id="EQ-A40-01",
            message="电动拧紧枪 #1 连续触发拧紧程序超差报警",
        ),
        _EventTemplate(
            offset_minutes=4,
            type=IndustrialEventType.PLC_ERROR,
            severity=RiskLevel.MEDIUM,
            equipment_id="EQ-A40-03",
            message="工位 PLC 记录连续拧紧结果超差",
        ),
    ),
    "ST-B30": (
        _EventTemplate(
            offset_minutes=-5,
            type=IndustrialEventType.MAINTENANCE,
            severity=RiskLevel.MEDIUM,
            equipment_id=None,
            message="扭矩工位预防性保养完成，参数待复核",
        ),
        _EventTemplate(
            offset_minutes=0,
            type=IndustrialEventType.TOOL_ERROR,
            severity=RiskLevel.MEDIUM,
            equipment_id=None,
            message="扭矩工位拧紧耗时超时，节拍延长",
        ),
    ),
}


def _unit_noise(*parts: str) -> float:
    """Stable pseudo-random value in [0, 1) for the given key parts."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0x1_0000_0000


def _align_to_bucket(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0) - timedelta(
        minutes=moment.minute % BUCKET_MINUTES
    )


class MockIndustrialDataProvider:
    def __init__(
        self, seed: int = 2026, fmea_records: Sequence[FmeaRecord] = ()
    ) -> None:
        self._seed = seed
        self._fmea_records = tuple(fmea_records)
        self._stations_by_line: dict[str, tuple[Station, ...]] = {}
        for line in _LINES:
            self._stations_by_line[line.line_id] = tuple(
                s for s in _STATIONS if s.line_id == line.line_id
            )

    # -- hierarchy -------------------------------------------------------- #

    async def get_factories(self) -> Sequence[Factory]:
        return _FACTORIES

    async def get_lines(self, factory_id: str) -> Sequence[Line]:
        return tuple(line for line in _LINES if line.factory_id == factory_id)

    async def get_stations(self, line_id: str) -> Sequence[Station]:
        return self._stations_by_line.get(line_id, ())

    async def get_equipment(self, station_id: str) -> Sequence[Equipment]:
        return tuple(e for e in _EQUIPMENT if e.station_id == station_id)

    async def get_processes(self, station_id: str) -> Sequence[Process]:
        return tuple(p for p in _PROCESSES if p.station_id == station_id)

    # -- time series ------------------------------------------------------ #

    async def get_metrics(
        self,
        *,
        metric: MetricKey,
        start: datetime,
        end: datetime,
        station_id: str | None = None,
        line_id: str | None = None,
    ) -> Sequence[MetricPoint]:
        if end <= start:
            return ()

        stations = self._resolve_stations(station_id=station_id, line_id=line_id)
        timestamps = self._buckets(start, end)
        unit = METRIC_UNITS[metric]

        points: list[MetricPoint] = []
        for station in stations:
            baseline = self._baseline(station.station_id, metric)
            amplitude = _NOISE_AMPLITUDE[metric]
            incidents = _INCIDENTS_BY_KEY.get((station.station_id, metric), ())
            for moment in timestamps:
                jitter = (_unit_noise(
                    str(self._seed),
                    station.station_id,
                    metric.value,
                    moment.isoformat(),
                ) - 0.5) * 2.0
                shift = 0.0
                if incidents:
                    shift = sum(item.peak_delta for item in incidents) * _incident_factor(moment)
                points.append(
                    MetricPoint(
                        timestamp=moment,
                        station_id=station.station_id,
                        metric=metric,
                        value=round(baseline + amplitude * jitter + shift, 3),
                        unit=unit,
                    )
                )
        points.sort(key=lambda p: (p.timestamp, p.station_id))
        return tuple(points)

    async def get_events(
        self,
        *,
        start: datetime,
        end: datetime,
        station_id: str | None = None,
        line_id: str | None = None,
        types: Sequence[IndustrialEventType] | None = None,
    ) -> Sequence[IndustrialEvent]:
        if end <= start:
            return ()

        in_scope = {
            station.station_id
            for station in self._resolve_stations(station_id=station_id, line_id=line_id)
        }
        wanted = set(types) if types else None

        events: list[IndustrialEvent] = []
        for onset in _incident_onsets(start, end):
            for chain_station, templates in _EVENT_CHAINS.items():
                if chain_station not in in_scope:
                    continue
                for template in templates:
                    moment = onset + timedelta(minutes=template.offset_minutes)
                    if not start <= moment <= end:
                        continue
                    if wanted is not None and template.type not in wanted:
                        continue
                    events.append(
                        IndustrialEvent(
                            event_id=(
                                f"EVT-{chain_station}-{template.type.value}"
                                f"-{moment.strftime('%Y%m%dT%H%M')}"
                            ),
                            timestamp=moment,
                            station_id=chain_station,
                            equipment_id=template.equipment_id,
                            type=template.type,
                            severity=template.severity,
                            message=template.message,
                        )
                    )
        events.sort(key=lambda item: (item.timestamp, item.station_id, item.event_id))
        return tuple(events)

    # -- reference data --------------------------------------------------- #

    async def get_fmea_records(self) -> Sequence[FmeaRecord]:
        return self._fmea_records

    # -- internals -------------------------------------------------------- #

    def _resolve_stations(
        self, *, station_id: str | None, line_id: str | None
    ) -> tuple[Station, ...]:
        if station_id:
            return tuple(s for s in _STATIONS if s.station_id == station_id)
        if line_id:
            return self._stations_by_line.get(line_id, ())
        return _STATIONS

    def _baseline(self, station_id: str, metric: MetricKey) -> float:
        override = _STATION_OVERRIDES.get(station_id, {})
        return override.get(metric, _DEFAULT_BASELINE[metric])

    @staticmethod
    def _buckets(start: datetime, end: datetime) -> tuple[datetime, ...]:
        first = _align_to_bucket(start)
        if first < start:
            first += BUCKET
        moments: list[datetime] = []
        current = first
        while current <= end and len(moments) < MAX_POINTS_PER_QUERY:
            moments.append(current)
            current += BUCKET
        return tuple(moments)