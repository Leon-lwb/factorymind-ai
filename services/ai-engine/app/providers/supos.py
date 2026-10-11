"""open supOS adapter (spec §22, §23).

`SupOsProvider` implements the same `IndustrialDataProvider` protocol the mock
does, so nothing above this layer changes when `DATA_PROVIDER=supos`. It reads
the plant hierarchy from the UNS tree and time series from the history query
API, then assembles them into the domain models the engine already consumes.
Events are *not* read over HTTP — supOS CE exposes no REST event-query endpoint,
so they are ingested from MQTT by `MqttBackedSupOsClient` (see `supos_mqtt.py`).

Two rules shape this module:

* **Never fabricate (spec §23).** If supOS is unreachable, a node is missing, or
  a wire value cannot be mapped to a domain enum, the adapter raises. It never
  degrades to synthetic data, because a demo that quietly serves mock numbers
  while claiming to read the plant is worse than a demo that fails loudly.
* **Never guess the API (spec §22).** The provider depends only on the
  `SupOsClient` protocol; the HTTP transport and its endpoint paths live in
  `HttpSupOsClient` / `SupOsEndpoints`, isolated so they can be corrected
  without touching any business logic. Every path in `SupOsEndpoints` is
  verified against the open supOS `V1.5.0.0-M` backend route table
  (`internal/interfaces/http/router.go`), not guessed — see
  `docs/SUPOS_INTEGRATION_CHECKLIST.md`.

FMEA is *our* reference data, not supOS data: like the mock, this provider is
handed the Master FMEA loaded from `config/master_fmea.json`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

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

# supOS rejects any UNS alias that does not match `^[A-Za-z_$][A-Za-z0-9_$]*$`
# (`normalizeCreateFileCommand` → `uns.alias.format.error`), so a dotted
# `{station}.{metric}` template can never resolve against a real instance. The
# separator is an underscore and the metric is slugged to an alias-safe token.
DEFAULT_METRIC_ALIAS_TEMPLATE = "{station}_{metric}"

# UNS file alias per metric, matching `config/uns_model.json`. Keep the two in
# sync — the model seeds these files and this adapter queries them.
METRIC_ALIAS_SLUGS: dict[MetricKey, str] = {
    MetricKey.FPY: "FPY",
    MetricKey.NOK_RATE: "NOK_RATE",
    MetricKey.TORQUE_NOK_RATE: "TORQUE_NOK_RATE",
    MetricKey.CYCLE_TIME: "CYCLE_TIME",
    MetricKey.TOOL_ERROR_RATE: "TOOL_ERROR_RATE",
}


class SupOsError(RuntimeError):
    """Base class for adapter failures."""


class SupOsUnavailableError(SupOsError):
    """supOS could not be reached or returned an unusable response."""


# --------------------------------------------------------------------------- #
# Normalised records — the boundary between the wire and the domain
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SupOsNode:
    """A UNS tree node with its kind normalised to our hierarchy vocabulary.

    `node_type` is one of `factory | line | station | equipment | process |
    other`. `parent_alias` carries the ISA-95 parent link so the provider can
    rebuild Factory → Line → Station → Equipment without a second lookup.
    """

    alias: str
    name: str
    node_type: str
    parent_alias: str | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SupOsSample:
    """One point of a UNS time-series alias."""

    timestamp: datetime
    alias: str
    value: float


@dataclass(frozen=True, slots=True)
class SupOsEventRecord:
    """One UNS event, keyed to the station alias it belongs to."""

    timestamp: datetime
    alias: str
    event_type: str
    severity: str
    message: str
    equipment_alias: str | None = None


# --------------------------------------------------------------------------- #
# Client protocol — the seam the provider depends on
# --------------------------------------------------------------------------- #


class SupOsClient(Protocol):
    """Everything the provider needs from supOS, in normalised form.

    Keeping this abstract is what makes the adapter testable without a live
    platform and keeps the (unverified) wire details out of the business logic.
    """

    async def list_nodes(self, root_alias: str | None = None) -> Sequence[SupOsNode]: ...

    async def query_timeseries(
        self, *, alias: str, start: datetime, end: datetime
    ) -> Sequence[SupOsSample]: ...

    async def query_events(
        self, *, start: datetime, end: datetime
    ) -> Sequence[SupOsEventRecord]: ...


# --------------------------------------------------------------------------- #
# HTTP transport
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SupOsEndpoints:
    """Endpoint paths on the supOS gateway (OpenResty, :8088).

    Every path is verified against the open supOS `V1.5.0.0-M` backend route
    table (`internal/interfaces/http/router.go`), not guessed. supOS CE exposes
    **no REST event-query endpoint**, so there is deliberately no `events` path
    here: events arrive over MQTT and are served by `MqttBackedSupOsClient`.
    """

    nodes: str = "/inter-api/supos/uns/tree"
    timeseries: str = "/inter-api/supos/uns/file/history/timeseries/query"
    current_values: str = "/inter-api/supos/uns/file/current/batchQuery"


class HttpSupOsClient:
    """`SupOsClient` over the supOS gateway REST API.

    The token is supplied by the caller (env `SUPOS_API_TOKEN`) and sent as the
    `supos_community_token` cookie, which is what the supOS OpenResty gateway
    actually reads for `/inter-api/supos/*` routes (it ignores the
    `Authorization` header). The Keycloak OAuth2 authorization-code flow is a
    deployment concern handled by the gateway/reverse proxy, not re-implemented
    here — see `docs/SUPOS_INTEGRATION.md` for the options.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        endpoints: SupOsEndpoints | None = None,
        timeout: float = 10.0,
        verify_tls: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("HttpSupOsClient requires a base_url")
        self._endpoints = endpoints or SupOsEndpoints()
        headers = {"Accept": "application/json"}
        if token:
            headers["Cookie"] = f"supos_community_token={token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            verify=verify_tls,
            headers=headers,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- reads ------------------------------------------------------------ #

    async def list_nodes(self, root_alias: str | None = None) -> Sequence[SupOsNode]:
        # `/uns/tree` walks the hierarchy by numeric parent id; without one it
        # returns the top level. The payload is nested, so it is flattened and
        # parent links are injected (children do not carry their parent's alias).
        params = {"parentId": root_alias} if root_alias else None
        payload = await self._request("GET", self._endpoints.nodes, params=params)
        return tuple(_parse_node(row) for row in _flatten_tree(_rows(payload)))

    async def query_timeseries(
        self, *, alias: str, start: datetime, end: datetime
    ) -> Sequence[SupOsSample]:
        payload = await self._request(
            "POST",
            self._endpoints.timeseries,
            json={"alias": alias, "startTime": _iso(start), "endTime": _iso(end)},
        )
        return tuple(_parse_sample(row, alias=alias) for row in _rows(payload))

    async def query_current_values(self, *, aliases: Sequence[str]) -> Sequence[SupOsSample]:
        payload = await self._request(
            "POST", self._endpoints.current_values, json=list(aliases)
        )
        return tuple(
            _parse_sample(row, alias=_first_str(row, "alias", "topic", "id") or "")
            for row in _rows(payload)
        )

    async def query_events(
        self, *, start: datetime, end: datetime
    ) -> Sequence[SupOsEventRecord]:
        raise SupOsUnavailableError(
            "supOS CE exposes no REST event-query endpoint; ingest events over "
            "MQTT with MqttBackedSupOsClient (SUPOS_EVENT_SOURCE=mqtt)"
        )

    # -- internals -------------------------------------------------------- #

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, object] | None = None,
    ) -> object:
        try:
            response = await self._client.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:  # connect/read/timeout — all "unavailable"
            raise SupOsUnavailableError(f"supOS request to {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise SupOsUnavailableError(
                f"supOS {method} {path} returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SupOsUnavailableError(f"supOS {method} {path} returned non-JSON body") from exc


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #


class SupOsProvider:
    """`IndustrialDataProvider` backed by open supOS (spec §22)."""

    def __init__(
        self,
        *,
        client: SupOsClient,
        fmea_records: Sequence[FmeaRecord] = (),
        uns_root_alias: str | None = None,
        metric_alias_template: str = DEFAULT_METRIC_ALIAS_TEMPLATE,
    ) -> None:
        self._client = client
        self._fmea_records = tuple(fmea_records)
        self._root_alias = uns_root_alias
        self._metric_alias_template = metric_alias_template

    async def astart(self) -> None:
        """Start any background work the client needs (e.g. an MQTT subscriber)."""
        starter = getattr(self._client, "astart", None)
        if starter is not None:
            await starter()

    async def aclose(self) -> None:
        closer = getattr(self._client, "aclose", None)
        if closer is not None:
            await closer()

    # -- hierarchy -------------------------------------------------------- #

    async def get_factories(self) -> Sequence[Factory]:
        return tuple(
            Factory(factory_id=node.alias, name=node.name, country=_country(node))
            for node in await self._nodes_of("factory")
        )

    async def get_lines(self, factory_id: str) -> Sequence[Line]:
        return tuple(
            Line(line_id=node.alias, factory_id=factory_id, name=node.name)
            for node in await self._nodes_of("line", parent_alias=factory_id)
        )

    async def get_stations(self, line_id: str) -> Sequence[Station]:
        return tuple(
            Station(station_id=node.alias, line_id=line_id, name=node.name)
            for node in await self._nodes_of("station", parent_alias=line_id)
        )

    async def get_equipment(self, station_id: str) -> Sequence[Equipment]:
        return tuple(
            Equipment(
                equipment_id=node.alias,
                station_id=station_id,
                type=_to_equipment_type(
                    node.attributes.get("equipmentType") or node.attributes.get("type")
                ),
                name=node.name,
            )
            for node in await self._nodes_of("equipment", parent_alias=station_id)
        )

    async def get_processes(self, station_id: str) -> Sequence[Process]:
        return tuple(
            Process(
                process_id=node.alias,
                station_id=station_id,
                name=node.name,
                operation=node.attributes.get("operation") or node.name,
            )
            for node in await self._nodes_of("process", parent_alias=station_id)
        )

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

        stations = await self._resolve_stations(station_id=station_id, line_id=line_id)
        unit = METRIC_UNITS[metric]

        points: list[MetricPoint] = []
        for station in stations:
            alias = self._metric_alias(station.alias, metric)
            samples = await self._client.query_timeseries(alias=alias, start=start, end=end)
            for sample in samples:
                if not start <= sample.timestamp <= end:
                    continue
                points.append(
                    MetricPoint(
                        timestamp=sample.timestamp,
                        station_id=station.alias,
                        metric=metric,
                        value=sample.value,
                        unit=unit,
                    )
                )
        points.sort(key=lambda point: (point.timestamp, point.station_id))
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

        stations = await self._resolve_stations(station_id=station_id, line_id=line_id)
        in_scope = {station.alias for station in stations}
        wanted = set(types) if types else None

        records = await self._client.query_events(start=start, end=end)
        events: list[IndustrialEvent] = []
        for record in records:
            if record.alias not in in_scope:
                continue
            if not start <= record.timestamp <= end:
                continue
            event_type = _to_event_type(record.event_type)
            if wanted is not None and event_type not in wanted:
                continue
            events.append(
                IndustrialEvent(
                    event_id=(
                        f"EVT-{record.alias}-{event_type.value}"
                        f"-{record.timestamp.strftime('%Y%m%dT%H%M')}"
                    ),
                    timestamp=record.timestamp,
                    station_id=record.alias,
                    equipment_id=record.equipment_alias,
                    type=event_type,
                    severity=_to_risk_level(record.severity),
                    message=record.message,
                )
            )
        events.sort(key=lambda item: (item.timestamp, item.station_id, item.event_id))
        return tuple(events)

    # -- reference data --------------------------------------------------- #

    async def get_fmea_records(self) -> Sequence[FmeaRecord]:
        return self._fmea_records

    # -- internals -------------------------------------------------------- #

    async def _list_nodes(self) -> tuple[SupOsNode, ...]:
        return tuple(await self._client.list_nodes(self._root_alias))

    async def _nodes_of(self, node_type: str, parent_alias: str | None = None) -> list[SupOsNode]:
        return [
            node
            for node in await self._list_nodes()
            if node.node_type == node_type
            and (parent_alias is None or node.parent_alias == parent_alias)
        ]

    async def _resolve_stations(
        self, *, station_id: str | None, line_id: str | None
    ) -> list[SupOsNode]:
        if station_id:
            return [node for node in await self._nodes_of("station") if node.alias == station_id]
        if line_id:
            return await self._nodes_of("station", parent_alias=line_id)
        return await self._nodes_of("station")

    def _metric_alias(self, station_alias: str, metric: MetricKey) -> str:
        return self._metric_alias_template.format(
            station=station_alias, metric=METRIC_ALIAS_SLUGS[metric]
        )


# --------------------------------------------------------------------------- #
# Wire parsing
# --------------------------------------------------------------------------- #

_NODE_TYPE_SYNONYMS: dict[str, str] = {
    "factory": "factory",
    "enterprise": "factory",
    "site": "factory",
    "plant": "factory",
    "line": "line",
    "area": "line",
    "productionline": "line",
    "station": "station",
    "workcenter": "station",
    "workstation": "station",
    "unit": "station",
    "cell": "station",
    "equipment": "equipment",
    "device": "equipment",
    "machine": "equipment",
    "process": "process",
    "operation": "process",
    "step": "process",
}


def _rows(payload: object) -> list[Mapping[str, object]]:
    """Extract the row list from a supOS response envelope.

    supOS wraps results in an envelope (`{code, data}`) whose inner key varies
    by endpoint, so the common shapes are accepted rather than pinned to one.
    """
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    if isinstance(payload, Mapping):
        for candidate in (payload.get("data"), payload):
            if isinstance(candidate, list):
                return [row for row in candidate if isinstance(row, Mapping)]
            if isinstance(candidate, Mapping):
                for key in ("records", "rows", "list", "items"):
                    nested = candidate.get(key)
                    if isinstance(nested, list):
                        return [row for row in nested if isinstance(row, Mapping)]
    raise SupOsError("unexpected supOS response shape: no row list found")


def _flatten_tree(
    rows: Sequence[Mapping[str, object]], *, parent_alias: str | None = None
) -> list[Mapping[str, object]]:
    """Flatten supOS `/uns/tree` (nested `children`) into a depth-first list.

    Nested children carry no `parentAlias`, so each one inherits its parent's
    alias here — the provider builds the hierarchy from that link.
    """
    flat: list[Mapping[str, object]] = []
    for row in rows:
        enriched = dict(row)
        if parent_alias is not None and not _first_str(
            row, "parentAlias", "parent", "parentId"
        ):
            enriched["parentAlias"] = parent_alias
        flat.append(enriched)
        children = row.get("children")
        if isinstance(children, list):
            flat.extend(
                _flatten_tree(
                    [child for child in children if isinstance(child, Mapping)],
                    parent_alias=_first_str(row, "alias", "topic", "id", "code"),
                )
            )
    return flat


def _parse_node(raw: Mapping[str, object]) -> SupOsNode:
    alias = _first_str(raw, "alias", "topic", "id", "code")
    if not alias:
        raise SupOsError("UNS node is missing an alias")
    parent = _first_str(raw, "parentAlias", "parent", "parentId")
    attributes = _node_attributes(raw)
    return SupOsNode(
        alias=alias,
        # supOS `/uns/tree` sets `name` to the path segment and `displayName` to
        # the operator-facing label, so the label is preferred for display.
        name=_first_str(raw, "displayName", "name", "title") or alias,
        node_type=_normalize_node_type(raw, attributes),
        parent_alias=parent,
        attributes=attributes,
    )


def _node_attributes(raw: Mapping[str, object]) -> dict[str, str]:
    """Flatten a node's custom properties.

    `/uns/tree` carries them under `extend` (the DB `extend` JSONB column, which
    supOS writes from `extendProperties` on create). There is no `attributes`
    key on the wire; it is accepted anyway so a caller with a normalised client
    can keep using it.
    """
    merged: dict[str, str] = {}
    for key in ("attributes", "extend", "extendProperties"):
        source = raw.get(key)
        if isinstance(source, Mapping):
            merged.update({str(k): str(v) for k, v in source.items()})
    return merged


def _parse_sample(raw: Mapping[str, object], *, alias: str) -> SupOsSample:
    return SupOsSample(
        timestamp=_parse_timestamp(_first_value(raw, "timestamp", "time", "ts")),
        alias=alias,
        value=_coerce_float(_first_value(raw, "value", "val")),
    )


def parse_event_row(raw: Mapping[str, object]) -> SupOsEventRecord:
    """Parse one event row from a supOS response (also reused by the MQTT path)."""
    return SupOsEventRecord(
        timestamp=_parse_timestamp(_first_value(raw, "timestamp", "time", "ts")),
        alias=_first_str(raw, "stationAlias", "alias", "topic", "nodeAlias") or "",
        event_type=_first_str(raw, "type", "eventType") or "",
        severity=_first_str(raw, "severity", "level") or "",
        message=_first_str(raw, "message", "description", "text") or "",
        equipment_alias=_first_str(raw, "equipmentAlias", "equipment", "deviceAlias"),
    )


def _normalize_node_type(raw: Mapping[str, object], attributes: Mapping[str, str]) -> str:
    """Map a UNS node to our hierarchy vocabulary.

    The kind comes from the model's own marker first: supOS `/uns/tree` reports
    `type` as a copy of `pathType` (0 = folder, 2 = file), which says nothing
    about whether a folder is a line or a station. `config/uns_model.json` sets
    `extendProperties.factorymindType` for exactly this reason.
    """
    kind = attributes.get("factorymindType") or _first_str(
        raw, "factorymindType", "type", "nodeType", "level"
    )
    key = str(kind or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    return _NODE_TYPE_SYNONYMS.get(key, "other")


def _country(node: SupOsNode) -> str:
    return node.attributes.get("country") or node.attributes.get("countryCode") or ""


def _first_value(raw: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def _first_str(raw: Mapping[str, object], *keys: str) -> str | None:
    value = _first_value(raw, *keys)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SupOsError(f"supOS returned a non-numeric metric value: {value!r}") from exc


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            raise SupOsError("supOS row is missing a timestamp")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise SupOsError(f"supOS returned an unparseable timestamp: {value!r}") from exc
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _to_equipment_type(value: object) -> EquipmentType:
    key = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    try:
        return EquipmentType(key)
    except ValueError as exc:
        raise SupOsError(
            f"supOS equipment node has no usable type (got {value!r}); expected one of "
            f"{', '.join(item.value for item in EquipmentType)}"
        ) from exc


def _to_event_type(value: object) -> IndustrialEventType:
    key = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    try:
        return IndustrialEventType(key)
    except ValueError as exc:
        raise SupOsError(f"supOS returned an unknown event type: {value!r}") from exc


def _to_risk_level(value: object) -> RiskLevel:
    key = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    try:
        return RiskLevel(key)
    except ValueError as exc:
        raise SupOsError(f"supOS returned an unknown severity: {value!r}") from exc