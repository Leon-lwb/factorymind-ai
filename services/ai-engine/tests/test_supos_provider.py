"""Tests for the open supOS adapter (spec §22, §23).

Two seams are exercised here:

* `SupOsProvider` against a fake `SupOsClient` — proves the UNS → domain mapping
  (hierarchy, alias convention, event scoping) without a live platform.
* `HttpSupOsClient` against `httpx.MockTransport` — proves the wire parsing and
  error handling, so the HTTP layer is verified even though no supOS instance is
  reachable in CI.

The adapter must never fabricate data: every "unusable input" case asserts a
loud `SupOsError` rather than a silent fallback to the mock.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import datetime

import httpx
import pytest
from app.config import load_assess_rules, load_settings
from app.domain.models import IndustrialEventType, MetricKey
from app.providers import create_data_provider
from app.providers.supos import (
    HttpSupOsClient,
    SupOsError,
    SupOsEventRecord,
    SupOsNode,
    SupOsProvider,
    SupOsSample,
    SupOsUnavailableError,
)

START = datetime(2026, 10, 1, 9, 0, 0)
END = datetime(2026, 10, 1, 11, 0, 0)

_NODES: tuple[SupOsNode, ...] = (
    SupOsNode(
        alias="FAC-01",
        name="示范总装工厂",
        node_type="factory",
        attributes={"country": "CN"},
    ),
    SupOsNode(alias="LINE-A", name="总装一线", node_type="line", parent_alias="FAC-01"),
    SupOsNode(alias="LINE-B", name="总装二线", node_type="line", parent_alias="FAC-01"),
    SupOsNode(alias="ST-A40", name="关键扭矩拧紧", node_type="station", parent_alias="LINE-A"),
    SupOsNode(alias="ST-A50", name="终检", node_type="station", parent_alias="LINE-A"),
    SupOsNode(
        alias="EQ-A40-01",
        name="电动拧紧枪 #1",
        node_type="equipment",
        parent_alias="ST-A40",
        attributes={"equipmentType": "TORQUE_TOOL"},
    ),
    SupOsNode(
        alias="PR-A40-01",
        name="关键扭矩拧紧",
        node_type="process",
        parent_alias="ST-A40",
        attributes={"operation": "拧紧"},
    ),
)


class FakeSupOsClient:
    """In-memory `SupOsClient`; records the aliases the provider asked for."""

    def __init__(
        self,
        *,
        nodes: Sequence[SupOsNode] = _NODES,
        samples: Sequence[SupOsSample] = (),
        events: Sequence[SupOsEventRecord] = (),
    ) -> None:
        self.nodes = list(nodes)
        self.samples = list(samples)
        self.events = list(events)
        self.queried_aliases: list[str] = []
        self.closed = False

    async def list_nodes(self, root_alias: str | None = None) -> Sequence[SupOsNode]:
        return list(self.nodes)

    async def query_timeseries(
        self, *, alias: str, start: datetime, end: datetime
    ) -> Sequence[SupOsSample]:
        self.queried_aliases.append(alias)
        return [s for s in self.samples if s.alias == alias and start <= s.timestamp <= end]

    async def query_events(
        self, *, start: datetime, end: datetime
    ) -> Sequence[SupOsEventRecord]:
        return [e for e in self.events if start <= e.timestamp <= end]

    async def aclose(self) -> None:
        self.closed = True


def _provider(client: FakeSupOsClient) -> SupOsProvider:
    return SupOsProvider(client=client)


# --------------------------------------------------------------------------- #
# Hierarchy
# --------------------------------------------------------------------------- #


def test_hierarchy_is_rebuilt_from_uns_parent_links() -> None:
    provider = _provider(FakeSupOsClient())

    factories = asyncio.run(provider.get_factories())
    assert [(f.factory_id, f.country) for f in factories] == [("FAC-01", "CN")]

    lines = asyncio.run(provider.get_lines("FAC-01"))
    assert [line.line_id for line in lines] == ["LINE-A", "LINE-B"]

    stations = asyncio.run(provider.get_stations("LINE-A"))
    assert [station.station_id for station in stations] == ["ST-A40", "ST-A50"]

    equipment = asyncio.run(provider.get_equipment("ST-A40"))
    assert [(e.equipment_id, e.type.value) for e in equipment] == [
        ("EQ-A40-01", "TORQUE_TOOL")
    ]

    processes = asyncio.run(provider.get_processes("ST-A40"))
    assert [(p.process_id, p.operation) for p in processes] == [("PR-A40-01", "拧紧")]


def test_hierarchy_is_scoped_to_the_requested_parent() -> None:
    provider = _provider(FakeSupOsClient())
    assert asyncio.run(provider.get_lines("FAC-UNKNOWN")) == ()
    assert asyncio.run(provider.get_stations("LINE-B")) == ()


def test_unknown_equipment_type_fails_loudly() -> None:
    nodes = (
        SupOsNode(
            alias="EQ-X",
            name="未知设备",
            node_type="equipment",
            parent_alias="ST-A40",
            attributes={},
        ),
    )
    provider = _provider(FakeSupOsClient(nodes=nodes))
    with pytest.raises(SupOsError):
        asyncio.run(provider.get_equipment("ST-A40"))


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


def test_metrics_use_alias_convention_and_metric_unit() -> None:
    samples = (
        SupOsSample(timestamp=START, alias="ST-A40_FPY", value=99.4),
        SupOsSample(timestamp=datetime(2026, 10, 1, 10, 0, 0), alias="ST-A40_FPY", value=97.2),
    )
    client = FakeSupOsClient(samples=samples)
    provider = _provider(client)

    points = asyncio.run(
        provider.get_metrics(metric=MetricKey.FPY, start=START, end=END, station_id="ST-A40")
    )

    assert client.queried_aliases == ["ST-A40_FPY"]
    assert [p.value for p in points] == [99.4, 97.2]
    assert all(p.unit == "%" and p.station_id == "ST-A40" for p in points)


def test_metrics_are_assembled_across_a_line_and_sorted() -> None:
    samples = (
        SupOsSample(
            timestamp=datetime(2026, 10, 1, 10, 0, 0), alias="ST-A50_CYCLE_TIME", value=64.0
        ),
        SupOsSample(
            timestamp=datetime(2026, 10, 1, 9, 30, 0), alias="ST-A40_CYCLE_TIME", value=48.0
        ),
    )
    client = FakeSupOsClient(samples=samples)
    provider = _provider(client)

    points = asyncio.run(
        provider.get_metrics(metric=MetricKey.CYCLE_TIME, start=START, end=END, line_id="LINE-A")
    )

    assert set(client.queried_aliases) == {"ST-A40_CYCLE_TIME", "ST-A50_CYCLE_TIME"}
    assert [(p.timestamp, p.station_id) for p in points] == [
        (datetime(2026, 10, 1, 9, 30, 0), "ST-A40"),
        (datetime(2026, 10, 1, 10, 0, 0), "ST-A50"),
    ]
    assert all(p.unit == "s" for p in points)


def test_metrics_return_empty_for_unknown_station_and_inverted_window() -> None:
    provider = _provider(FakeSupOsClient(samples=(SupOsSample(START, "ST-A40_FPY", 99.0),)))

    assert (
        asyncio.run(
            provider.get_metrics(metric=MetricKey.FPY, start=START, end=END, station_id="ST-XX")
        )
        == ()
    )
    assert (
        asyncio.run(
            provider.get_metrics(metric=MetricKey.FPY, start=END, end=START, station_id="ST-A40")
        )
        == ()
    )


def test_metrics_drop_samples_outside_the_window() -> None:
    samples = (
        SupOsSample(timestamp=datetime(2026, 10, 1, 8, 0, 0), alias="ST-A40_FPY", value=1.0),
        SupOsSample(timestamp=datetime(2026, 10, 1, 10, 0, 0), alias="ST-A40_FPY", value=2.0),
    )
    provider = _provider(FakeSupOsClient(samples=samples))

    points = asyncio.run(
        provider.get_metrics(metric=MetricKey.FPY, start=START, end=END, station_id="ST-A40")
    )
    assert [p.value for p in points] == [2.0]


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


def test_events_are_scoped_to_the_station_and_filtered_by_type() -> None:
    events = (
        SupOsEventRecord(
            timestamp=datetime(2026, 10, 1, 9, 30, 0),
            alias="ST-A40",
            event_type="TOOL_ERROR",
            severity="HIGH",
            message="拧紧枪超差报警",
            equipment_alias="EQ-A40-01",
        ),
        SupOsEventRecord(
            timestamp=datetime(2026, 10, 1, 9, 40, 0),
            alias="ST-OTHER",
            event_type="TOOL_ERROR",
            severity="LOW",
            message="其它工位",
        ),
    )
    provider = _provider(FakeSupOsClient(events=events))

    scoped = asyncio.run(
        provider.get_events(start=START, end=END, station_id="ST-A40")
    )
    assert [e.station_id for e in scoped] == ["ST-A40"]
    assert scoped[0].severity.value == "HIGH"
    assert scoped[0].equipment_id == "EQ-A40-01"

    filtered = asyncio.run(
        provider.get_events(
            start=START, end=END, station_id="ST-A40", types=[IndustrialEventType.PLC_ERROR]
        )
    )
    assert filtered == ()


def test_unknown_event_type_fails_loudly() -> None:
    events = (
        SupOsEventRecord(
            timestamp=datetime(2026, 10, 1, 9, 30, 0),
            alias="ST-A40",
            event_type="NOT_A_REAL_TYPE",
            severity="HIGH",
            message="x",
        ),
    )
    provider = _provider(FakeSupOsClient(events=events))
    with pytest.raises(SupOsError):
        asyncio.run(provider.get_events(start=START, end=END, station_id="ST-A40"))


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_provider_releases_the_underlying_client() -> None:
    client = FakeSupOsClient()
    asyncio.run(_provider(client).aclose())
    assert client.closed is True


# --------------------------------------------------------------------------- #
# Factory wiring
# --------------------------------------------------------------------------- #


def test_factory_requires_a_url_for_supos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_PROVIDER", "supos")
    monkeypatch.delenv("SUPOS_API_URL", raising=False)
    with pytest.raises(ValueError, match="SUPOS_API_URL"):
        create_data_provider(load_settings(), load_assess_rules())


def test_factory_builds_a_supos_provider_with_master_fmea(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_PROVIDER", "supos")
    monkeypatch.setenv("SUPOS_API_URL", "http://supos.test:8088")
    monkeypatch.setenv("SUPOS_MQTT_URL", "mqtt://emqx.test:1883")

    provider = create_data_provider(load_settings(), load_assess_rules())

    assert isinstance(provider, SupOsProvider)
    assert len(asyncio.run(provider.get_fmea_records())) > 0
    asyncio.run(provider.aclose())


# --------------------------------------------------------------------------- #
# HTTP transport (no network: httpx.MockTransport)
# --------------------------------------------------------------------------- #


def _http_client(handler: Callable[[httpx.Request], httpx.Response]) -> HttpSupOsClient:
    return HttpSupOsClient(
        base_url="http://supos.test:8088",
        token="secret",
        transport=httpx.MockTransport(handler),
    )


def test_http_client_flattens_uns_tree_and_sends_the_community_token_cookie() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["cookie"] == "supos_community_token=secret"
        assert request.url.path == "/inter-api/supos/uns/condition/tree"
        # The CE build caps the GET tree at 10 rows; the condition-tree route
        # honours this body's pageSize, so a large page is requested explicitly.
        assert json.loads(request.content)["pageSize"] >= 100
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {
                        "alias": "FAC-01",
                        "name": "工厂",
                        "type": "factory",
                        "children": [
                            {"alias": "LINE-A", "name": "总装一线", "type": "line"}
                        ],
                    }
                ],
            },
        )

    client = _http_client(handler)
    nodes = asyncio.run(client.list_nodes())
    asyncio.run(client.aclose())

    # The nested tree is flattened and the child inherits its parent's alias.
    assert [(n.alias, n.node_type, n.parent_alias) for n in nodes] == [
        ("FAC-01", "factory", None),
        ("LINE-A", "line", "FAC-01"),
    ]


def test_http_client_posts_aliases_to_current_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/inter-api/supos/uns/file/current/batchQuery"
        assert json.loads(request.content) == ["ST-A40_FPY", "ST-A40_NOK"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"alias": "ST-A40_FPY", "timestamp": "2026-10-01T09:30:00Z", "value": "99.1"},
                    {"alias": "ST-A40_NOK", "timestamp": "2026-10-01T09:30:00Z", "value": 0.9},
                ]
            },
        )

    client = _http_client(handler)
    samples = asyncio.run(
        client.query_current_values(aliases=["ST-A40_FPY", "ST-A40_NOK"])
    )
    asyncio.run(client.aclose())

    assert [(s.alias, s.value) for s in samples] == [("ST-A40_FPY", 99.1), ("ST-A40_NOK", 0.9)]


def test_http_client_parses_timeseries_and_coerces_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["alias"] == "ST-A40_FPY"
        # supOS rejects a bare timestamp (HTTP 400); the zone is made explicit.
        assert body["startTime"] == f"{START.isoformat()}+00:00"
        # The 20-row default page would truncate an 8h window.
        assert body["pageSize"] >= 100
        return httpx.Response(
            200,
            json={
                "data": [
                    {"timestamp": "2026-10-01T09:30:00Z", "value": "99.1"},
                    {"timestamp": "2026-10-01T10:00:00+08:00", "value": 97.5},
                ]
            },
        )

    client = _http_client(handler)
    samples = asyncio.run(client.query_timeseries(alias="ST-A40_FPY", start=START, end=END))
    asyncio.run(client.aclose())

    # `Z` and `+08:00` are both normalised to naive UTC.
    assert [s.timestamp for s in samples] == [
        datetime(2026, 10, 1, 9, 30, 0),
        datetime(2026, 10, 1, 2, 0, 0),
    ]
    assert [s.value for s in samples] == [99.1, 97.5]


def test_http_client_refuses_rest_event_query() -> None:
    # supOS CE has no REST event-query endpoint; the HTTP client must say so
    # loudly instead of hitting a path that does not exist.
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP request should be issued for events")

    client = _http_client(handler)
    with pytest.raises(SupOsUnavailableError):
        asyncio.run(client.query_events(start=START, end=END))
    asyncio.run(client.aclose())


def test_http_client_raises_on_error_status_and_bad_json() -> None:
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="gateway down")

    client = _http_client(error_handler)
    with pytest.raises(SupOsUnavailableError):
        asyncio.run(client.query_timeseries(alias="ST-A40_FPY", start=START, end=END))
    asyncio.run(client.aclose())

    def bad_json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = _http_client(bad_json_handler)
    with pytest.raises(SupOsUnavailableError):
        asyncio.run(client.list_nodes())
    asyncio.run(client.aclose())


def test_http_client_rejects_an_unrecognisable_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "message": "ok"})

    client = _http_client(handler)
    with pytest.raises(SupOsError):
        asyncio.run(client.list_nodes())
    asyncio.run(client.aclose())