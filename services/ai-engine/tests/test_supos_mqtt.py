"""Tests for the MQTT event path of the supOS adapter (spec §22).

The subscriber is exercised through an injected fake connector, so the ingest
loop, buffer, payload parsing and lifecycle are all verified without a broker or
a live `aiomqtt` connection.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

import pytest
from app.config import load_assess_rules, load_settings
from app.domain.models import IndustrialEventType
from app.providers import create_data_provider
from app.providers.supos import (
    SupOsError,
    SupOsEventRecord,
    SupOsNode,
    SupOsProvider,
    SupOsSample,
)
from app.providers.supos_mqtt import (
    EventBuffer,
    MqttBackedSupOsClient,
    MqttEventIngestor,
    MqttEventSettings,
    MqttMessage,
    parse_event_payload,
)

START = datetime(2026, 10, 1, 9, 0, 0)
END = datetime(2026, 10, 1, 11, 0, 0)


def _event_payload(
    *,
    station_alias: str | None = "ST-A40",
    timestamp: str = "2026-10-01T09:30:00",
    event_type: str = "TOOL_ERROR",
    severity: str = "HIGH",
) -> bytes:
    body: dict[str, object] = {
        "timestamp": timestamp,
        "eventType": event_type,
        "severity": severity,
        "message": "拧紧枪超差报警",
    }
    if station_alias is not None:
        body["stationAlias"] = station_alias
    return json.dumps(body).encode("utf-8")


def _connector(messages: Sequence[MqttMessage]):
    async def _generate(settings: MqttEventSettings) -> AsyncIterator[MqttMessage]:
        for message in messages:
            yield message

    return _generate


class _StubHttpClient:
    """Minimal `SupOsClient` stand-in for the HTTP half of the hybrid client."""

    def __init__(self, nodes: Sequence[SupOsNode] = ()) -> None:
        self.nodes = list(nodes)
        self.closed = False

    async def list_nodes(self, root_alias: str | None = None) -> Sequence[SupOsNode]:
        return list(self.nodes)

    async def query_timeseries(self, *, alias: str, start: datetime, end: datetime):
        return [SupOsSample(timestamp=START, alias=alias, value=99.0)]

    async def aclose(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# Settings parsing
# --------------------------------------------------------------------------- #


def test_settings_from_url_selects_tls_and_default_port() -> None:
    plain = MqttEventSettings.from_url("mqtt://emqx.test:1883", topic="a/#")
    assert (plain.host, plain.port, plain.tls) == ("emqx.test", 1883, False)
    assert plain.topic == "a/#"

    secure = MqttEventSettings.from_url("mqtts://emqx.test")
    assert (secure.host, secure.port, secure.tls) == ("emqx.test", 8883, True)


def test_settings_from_url_reads_credentials_and_rejects_bad_urls() -> None:
    settings = MqttEventSettings.from_url("mqtt://user:pass@emqx.test:1883")
    assert (settings.username, settings.password) == ("user", "pass")

    with pytest.raises(SupOsError, match="scheme"):
        MqttEventSettings.from_url("http://emqx.test")
    with pytest.raises(SupOsError, match="host"):
        MqttEventSettings.from_url("mqtt:///no-host")


# --------------------------------------------------------------------------- #
# Payload parsing
# --------------------------------------------------------------------------- #


def test_parse_event_payload_uses_the_payload_alias() -> None:
    record = parse_event_payload("factorymind/uns/anything", _event_payload())
    assert (record.alias, record.event_type, record.severity) == ("ST-A40", "TOOL_ERROR", "HIGH")
    assert record.timestamp == datetime(2026, 10, 1, 9, 30, 0)


def test_parse_event_payload_falls_back_to_the_topic_segment() -> None:
    record = parse_event_payload(
        "factorymind/uns/ST-A40/events", _event_payload(station_alias=None)
    )
    # No payload alias → the last non-wildcard topic segment is used.
    assert record.alias == "events"


def test_parse_event_payload_rejects_unusable_bodies() -> None:
    with pytest.raises(SupOsError, match="not JSON"):
        parse_event_payload("t", b"<xml/>")
    with pytest.raises(SupOsError, match="JSON object"):
        parse_event_payload("t", b"[1, 2, 3]")


# --------------------------------------------------------------------------- #
# Buffer
# --------------------------------------------------------------------------- #


def _record(moment: datetime, alias: str = "ST-A40") -> SupOsEventRecord:
    return SupOsEventRecord(
        timestamp=moment, alias=alias, event_type="TOOL_ERROR", severity="HIGH", message="x"
    )


def test_buffer_is_bounded_and_windowed() -> None:
    buffer = EventBuffer(maxlen=2)
    buffer.append(_record(datetime(2026, 10, 1, 9, 30, 0)))
    buffer.append(_record(datetime(2026, 10, 1, 10, 0, 0)))
    buffer.append(_record(datetime(2026, 10, 1, 10, 30, 0)))

    assert len(buffer) == 2  # oldest evicted
    assert [r.timestamp for r in buffer.query(START, END)] == [
        datetime(2026, 10, 1, 10, 0, 0),
        datetime(2026, 10, 1, 10, 30, 0),
    ]
    assert buffer.query(datetime(2026, 10, 2), datetime(2026, 10, 3)) == []


# --------------------------------------------------------------------------- #
# Ingestor
# --------------------------------------------------------------------------- #


def test_ingestor_fills_the_buffer_and_drops_bad_messages() -> None:
    messages = (
        MqttMessage("factorymind/uns/ST-A40", _event_payload()),
        MqttMessage("factorymind/uns/ST-A40", b"not-json"),
        MqttMessage(
            "factorymind/uns/ST-A40",
            _event_payload(event_type="PLC_ERROR", timestamp="2026-10-01T10:00:00"),
        ),
    )
    buffer = EventBuffer(maxlen=10)
    ingestor = MqttEventIngestor(
        MqttEventSettings(host="broker"), buffer, connector=_connector(messages)
    )

    async def scenario() -> Sequence[SupOsEventRecord]:
        await ingestor.start()
        for _ in range(200):
            if len(buffer) >= 2:
                break
            await asyncio.sleep(0.005)
        await ingestor.stop()
        return buffer.query(START, END)

    records = asyncio.run(scenario())

    assert len(records) == 2  # the malformed message was dropped, not invented
    assert {r.event_type for r in records} == {"TOOL_ERROR", "PLC_ERROR"}


# --------------------------------------------------------------------------- #
# Hybrid client
# --------------------------------------------------------------------------- #


def test_hybrid_client_splits_http_and_mqtt_and_manages_lifecycle() -> None:
    buffer = EventBuffer(maxlen=10)
    buffer.append(_record(datetime(2026, 10, 1, 9, 30, 0)))
    http = _StubHttpClient(nodes=[SupOsNode("FAC-01", "工厂", "factory")])
    client = MqttBackedSupOsClient(
        http=http,
        ingestor=MqttEventIngestor(
            MqttEventSettings(host="broker"), buffer, connector=_connector(())
        ),
        buffer=buffer,
    )

    async def scenario():
        await client.astart()
        nodes = await client.list_nodes()
        samples = await client.query_timeseries(alias="ST-A40_FPY", start=START, end=END)
        events = await client.query_events(start=START, end=END)
        await client.aclose()
        return nodes, samples, events

    nodes, samples, events = asyncio.run(scenario())

    assert [n.alias for n in nodes] == ["FAC-01"]
    assert [s.value for s in samples] == [99.0]
    assert len(events) == 1
    assert http.closed is True


def test_provider_maps_buffered_events_end_to_end() -> None:
    buffer = EventBuffer(maxlen=10)
    buffer.append(_record(datetime(2026, 10, 1, 9, 30, 0)))
    http = _StubHttpClient(
        nodes=[SupOsNode("ST-A40", "关键扭矩拧紧", "station", parent_alias="LINE-A")]
    )
    provider = SupOsProvider(
        client=MqttBackedSupOsClient(
            http=http,
            ingestor=MqttEventIngestor(
                MqttEventSettings(host="broker"), buffer, connector=_connector(())
            ),
            buffer=buffer,
        )
    )

    events = asyncio.run(
        provider.get_events(
            start=START, end=END, station_id="ST-A40", types=[IndustrialEventType.TOOL_ERROR]
        )
    )
    asyncio.run(provider.aclose())

    assert [e.station_id for e in events] == ["ST-A40"]
    assert events[0].severity.value == "HIGH"


# --------------------------------------------------------------------------- #
# Factory wiring
# --------------------------------------------------------------------------- #


def _supos_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_PROVIDER", "supos")
    monkeypatch.setenv("SUPOS_API_URL", "http://supos.test:8088")


def test_factory_defaults_to_mqtt_events(monkeypatch: pytest.MonkeyPatch) -> None:
    _supos_env(monkeypatch)
    monkeypatch.delenv("SUPOS_EVENT_SOURCE", raising=False)
    monkeypatch.setenv("SUPOS_MQTT_URL", "mqtt://emqx.test:1883")

    provider = create_data_provider(load_settings(), load_assess_rules())

    assert isinstance(provider._client, MqttBackedSupOsClient)  # type: ignore[attr-defined]
    asyncio.run(provider.aclose())


def test_factory_rejects_the_unsupported_rest_event_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # supOS CE has no REST event endpoint, so `rest` must fail loudly at startup.
    _supos_env(monkeypatch)
    monkeypatch.setenv("SUPOS_EVENT_SOURCE", "rest")

    with pytest.raises(ValueError, match="rest"):
        create_data_provider(load_settings(), load_assess_rules())


def test_factory_builds_mqtt_backed_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _supos_env(monkeypatch)
    monkeypatch.setenv("SUPOS_EVENT_SOURCE", "mqtt")
    monkeypatch.setenv("SUPOS_MQTT_URL", "mqtt://emqx.test:1883")

    provider = create_data_provider(load_settings(), load_assess_rules())

    assert isinstance(provider._client, MqttBackedSupOsClient)  # type: ignore[attr-defined]
    asyncio.run(provider.aclose())


def test_factory_requires_mqtt_url_when_source_is_mqtt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _supos_env(monkeypatch)
    monkeypatch.setenv("SUPOS_EVENT_SOURCE", "mqtt")
    monkeypatch.delenv("SUPOS_MQTT_URL", raising=False)

    with pytest.raises(ValueError, match="SUPOS_MQTT_URL"):
        create_data_provider(load_settings(), load_assess_rules())


def test_factory_rejects_an_unknown_event_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _supos_env(monkeypatch)
    monkeypatch.setenv("SUPOS_EVENT_SOURCE", "carrier-pigeon")

    with pytest.raises(ValueError, match="SUPOS_EVENT_SOURCE"):
        create_data_provider(load_settings(), load_assess_rules())