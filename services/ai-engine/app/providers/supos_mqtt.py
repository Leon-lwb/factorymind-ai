"""MQTT event ingestion for the open supOS adapter (spec §22, §23).

open supOS's realtime path is MQTT (EMQX) with `external/topic2Uns` bridging
external topics into the UNS. This module turns that push stream into the
pull-based `query_events` the `IndustrialDataProvider` protocol expects:

```
EMQX topic ──▶ MqttEventIngestor ──▶ EventBuffer ──▶ MqttBackedSupOsClient.query_events
```

The provider's contract is a time-window query, so a short bounded buffer stands
in for the stream. `MqttBackedSupOsClient` keeps HTTP for the UNS tree and history
(a pull API), and reads events from this buffer.

Design notes:

* **Injectable connector.** The ingestor depends on a `MqttConnector` — an async
  iterator of `MqttMessage`. The default connector uses `aiomqtt`, imported
  lazily so this module loads (and tests run) without a broker or the library.
* **Never fabricate.** An unparseable payload raises inside the ingest loop; the
  loop logs and reconnects, and the bad message is dropped rather than turned
  into a fake event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit

from app.providers.supos import (
    SupOsClient,
    SupOsError,
    SupOsEventRecord,
    parse_event_row,
)

logger = logging.getLogger(__name__)

DEFAULT_TOPIC = "factorymind/uns/#"


@dataclass(frozen=True, slots=True)
class MqttEventSettings:
    """Connection and buffering settings for the event subscriber."""

    host: str
    port: int = 1883
    topic: str = DEFAULT_TOPIC
    username: str | None = None
    password: str | None = None
    tls: bool = False
    keepalive: int = 30
    client_id: str = "factorymind-ai-engine"
    buffer_size: int = 5_000
    reconnect_seconds: float = 5.0

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        topic: str = DEFAULT_TOPIC,
        username: str | None = None,
        password: str | None = None,
    ) -> MqttEventSettings:
        """Parse `mqtt://host:port` / `mqtts://host` into settings.

        The scheme selects TLS and the default port; credentials in the URL are
        used only if no explicit username/password is supplied.
        """
        parts = urlsplit(url)
        if parts.scheme not in {"mqtt", "mqtts", "tcp", "ssl"}:
            raise SupOsError(f"unsupported MQTT URL scheme: {parts.scheme!r}")
        if not parts.hostname:
            raise SupOsError(f"MQTT URL has no host: {url!r}")
        tls = parts.scheme in {"mqtts", "ssl"}
        return cls(
            host=parts.hostname,
            port=parts.port or (8883 if tls else 1883),
            topic=topic,
            username=username or parts.username,
            password=password or parts.password,
            tls=tls,
        )


@dataclass(frozen=True, slots=True)
class MqttMessage:
    topic: str
    payload: bytes


# An async iterator factory: given settings, yield messages until the connection
# ends or the caller stops iterating.
MqttConnector = Callable[[MqttEventSettings], AsyncIterator[MqttMessage]]


class EventBuffer:
    """Bounded, time-windowed store of ingested events.

    Old events are evicted once `maxlen` is exceeded, which bounds memory for a
    long-running subscriber while keeping the recent window the demo queries.
    """

    def __init__(self, *, maxlen: int = 5_000) -> None:
        if maxlen <= 0:
            raise ValueError("EventBuffer maxlen must be positive")
        self._events: deque[SupOsEventRecord] = deque(maxlen=maxlen)

    def append(self, record: SupOsEventRecord) -> None:
        self._events.append(record)

    def query(self, start: datetime, end: datetime) -> Sequence[SupOsEventRecord]:
        return [event for event in self._events if start <= event.timestamp <= end]

    def __len__(self) -> int:
        return len(self._events)


def parse_event_payload(topic: str, payload: bytes | str) -> SupOsEventRecord:
    """Decode one MQTT event message into a `SupOsEventRecord`.

    The station alias comes from the payload (`stationAlias`); if absent, the
    last non-empty topic segment is used as a fallback. A payload that is not a
    JSON object raises — the caller drops it rather than inventing an event.
    """
    text = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else str(payload)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SupOsError(f"MQTT event payload is not JSON: {text[:120]!r}") from exc
    if not isinstance(raw, Mapping):
        raise SupOsError("MQTT event payload must be a JSON object")

    record = parse_event_row(raw)
    if record.alias:
        return record
    alias = _alias_from_topic(topic)
    if not alias:
        raise SupOsError(f"MQTT event carries no station alias (topic={topic!r})")
    return replace(record, alias=alias)


class MqttEventIngestor:
    """Subscribes to MQTT and appends parsed events to an `EventBuffer`."""

    def __init__(
        self,
        settings: MqttEventSettings,
        buffer: EventBuffer,
        *,
        connector: MqttConnector | None = None,
    ) -> None:
        self._settings = settings
        self._buffer = buffer
        self._connector = connector or _aiomqtt_messages
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="supos-mqtt-events")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                async for message in self._connector(self._settings):
                    try:
                        self._buffer.append(parse_event_payload(message.topic, message.payload))
                    except SupOsError as exc:
                        # One bad message must not tear down the subscription.
                        logger.warning("dropping unparseable MQTT event: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # connection loss, broker restart, ...
                logger.warning("MQTT event stream error, reconnecting: %s", exc)
            await asyncio.sleep(self._settings.reconnect_seconds)


async def _aiomqtt_messages(settings: MqttEventSettings) -> AsyncIterator[MqttMessage]:
    """Default connector: `aiomqtt`, imported lazily so the module loads without it."""
    import aiomqtt

    tls_context = ssl.create_default_context() if settings.tls else None
    async with aiomqtt.Client(
        hostname=settings.host,
        port=settings.port,
        username=settings.username,
        password=settings.password,
        identifier=settings.client_id,
        keepalive=settings.keepalive,
        tls_context=tls_context,
    ) as client:
        await client.subscribe(settings.topic)
        async for message in client.messages:
            yield MqttMessage(topic=str(message.topic), payload=bytes(message.payload))


class MqttBackedSupOsClient:
    """`SupOsClient` that reads the UNS tree/history over HTTP and events over MQTT.

    `astart` launches the subscriber; the FastAPI lifespan calls it on startup
    and `aclose` on shutdown, mirroring the plain HTTP client's lifecycle.
    """

    def __init__(
        self,
        *,
        http: SupOsClient,
        ingestor: MqttEventIngestor,
        buffer: EventBuffer,
    ) -> None:
        self._http = http
        self._ingestor = ingestor
        self._buffer = buffer

    async def astart(self) -> None:
        await self._ingestor.start()

    async def aclose(self) -> None:
        await self._ingestor.stop()
        closer = getattr(self._http, "aclose", None)
        if closer is not None:
            await closer()

    async def list_nodes(self, root_alias: str | None = None):
        return await self._http.list_nodes(root_alias)

    async def query_timeseries(self, *, alias: str, start: datetime, end: datetime):
        return await self._http.query_timeseries(alias=alias, start=start, end=end)

    async def query_current_values(self, *, aliases):
        return await self._http.query_current_values(aliases=aliases)

    async def query_events(self, *, start: datetime, end: datetime):
        return self._buffer.query(start, end)


def _alias_from_topic(topic: str) -> str | None:
    segments = [segment for segment in topic.split("/") if segment and segment not in {"#", "+"}]
    return segments[-1] if segments else None