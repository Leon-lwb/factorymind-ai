"""Verify the open supOS integration against a live deployment (spec §22).

The adapter ships with *candidate* endpoint paths: the repository research
confirmed the history query, but not the UNS node or event read surfaces. This
script is the missing half of that research — point it at a real gateway and it
reports, without guessing, which paths exist, whether the adapter can parse what
they return, and what the MQTT event stream actually carries.

Nothing here fabricates a pass. A path that 404s is reported as a failure, a
reachable path that rejects the token is reported as an auth failure, and a
response the adapter cannot parse is reported as a shape mismatch. When a
working path differs from `SupOsEndpoints`, the exact replacement line is
printed for you to paste in.

Run:
    python scripts/verify_supos.py --base-url http://<host>:8088 --token <bearer>
    python scripts/verify_supos.py --base-url ... --mqtt-url mqtt://<host>:1883

Environment fallbacks: SUPOS_API_URL, SUPOS_API_TOKEN, SUPOS_TIMEOUT_SECONDS,
SUPOS_VERIFY_TLS, SUPOS_UNS_ROOT_ALIAS, SUPOS_MQTT_URL, SUPOS_MQTT_TOPIC,
SUPOS_USERNAME, SUPOS_PASSWORD.

With `--json`, the report can embed real plant payloads. It is written only
where you point it — keep it out of the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Run as `python scripts/verify_supos.py` puts scripts/ on sys.path, not the
# service root, so `app` would not resolve. Add the root explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from app.config import Settings, load_settings
from app.providers.supos import (
    HttpSupOsClient,
    SupOsEndpoints,
    SupOsError,
    SupOsProvider,
    SupOsUnavailableError,
    _rows,  # the adapter's own envelope parser: reuse it so the check cannot drift
)
from app.providers.supos_mqtt import MqttEventSettings, parse_event_payload

# --------------------------------------------------------------------------- #
# Discovery / probing constants
# --------------------------------------------------------------------------- #

# Swagger/OpenAPI document locations. The deployed instance is authoritative —
# these are the standard Springdoc paths for the open supOS gateway.
OPENAPI_CANDIDATES = ("/v3/api-docs", "/v3/api-docs/", "/api-docs", "/openapi.json")

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# Paths are printed for every UNS-related operation the OpenAPI document lists;
# the keyword set is deliberately broad so nothing relevant is hidden.
DISCOVERY_KEYWORDS = ("uns", "model", "node", "event", "timeseries", "history", "alias")

# Fallbacks probed only when OpenAPI is not exposed. These are *candidates*, not
# facts, and are labelled as such in the report.
NODE_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("GET", "/inter-api/supos/uns/tree"),
    ("POST", "/inter-api/supos/uns/condition/tree"),
    ("GET", "/inter-api/supos/uns/model/tree"),
)

# Events are deliberately absent: supOS CE exposes no REST event-query endpoint
# (verified against the V1.5.0.0-M route table). Events are ingested over MQTT —
# see the MQTT probe in [4] below.

HISTORY_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("POST", "/inter-api/supos/uns/file/history/timeseries/query"),
)

CURRENT_VALUE_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("POST", "/inter-api/supos/uns/file/current/batchQuery"),
    ("POST", "/inter-api/supos/uns/file/current/batchQuery/byPath"),
    ("POST", "/inter-api/supos/uns/file/batchQuery"),
)

MAX_PROBES_PER_TARGET = 8


# --------------------------------------------------------------------------- #
# Small result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Probe:
    method: str
    path: str
    status: int | None
    error: str | None
    rows: int | None
    note: str = ""

    @property
    def state(self) -> str:
        if self.error is not None or self.status is None:
            return "FAIL"
        if self.status >= 400:
            return "FAIL"
        if self.rows is None:
            return "WARN"
        return "OK"


@dataclass(frozen=True, slots=True)
class Target:
    key: str
    label: str
    configured: tuple[str, str]
    fallbacks: tuple[tuple[str, str], ...]
    body: str  # 'nodes' | 'timeseries' | 'events'


@dataclass(slots=True)
class TargetResult:
    target: Target
    probes: list[Probe] = field(default_factory=list)

    @property
    def configured_probe(self) -> Probe | None:
        for probe in self.probes:
            if (probe.method, probe.path) == self.target.configured:
                return probe
        return None

    @property
    def best(self) -> Probe | None:
        for state in ("OK", "WARN"):
            for probe in self.probes:
                if probe.state == state:
                    return probe
        return None


@dataclass(slots=True)
class MqttProbe:
    connected: bool = False
    error: str | None = None
    message_count: int = 0
    topics: dict[str, int] = field(default_factory=dict)
    payload_keys: dict[str, int] = field(default_factory=dict)
    parsed: int = 0
    unparsed: list[str] = field(default_factory=list)
    event_types: dict[str, int] = field(default_factory=dict)
    severities: dict[str, int] = field(default_factory=dict)
    samples: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Target definitions
# --------------------------------------------------------------------------- #


def build_targets() -> tuple[Target, ...]:
    defaults = SupOsEndpoints()
    return (
        Target(
            key="nodes",
            label="UNS node read",
            configured=("GET", defaults.nodes),
            fallbacks=NODE_FALLBACKS,
            body="nodes",
        ),
        Target(
            key="timeseries",
            label="history timeseries query",
            configured=("POST", defaults.timeseries),
            fallbacks=HISTORY_FALLBACKS,
            body="timeseries",
        ),
        Target(
            key="current_values",
            label="current values batch query",
            configured=("POST", defaults.current_values),
            fallbacks=CURRENT_VALUE_FALLBACKS,
            body="current_values",
        ),
    )


# --------------------------------------------------------------------------- #
# HTTP probing
# --------------------------------------------------------------------------- #


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _body_for(
    kind: str, *, alias: str, start: datetime, end: datetime, root_alias: str | None
) -> tuple[Mapping[str, str] | None, object]:
    if kind == "nodes":
        return ({"parentId": root_alias} if root_alias else None), None
    if kind == "timeseries":
        return None, {"alias": alias, "startTime": _iso(start), "endTime": _iso(end)}
    if kind == "current_values":
        return None, [alias]
    raise ValueError(f"unknown probe body kind: {kind!r}")


def _row_count(payload: object) -> int | None:
    try:
        return len(_rows(payload))
    except SupOsError:
        return None


async def _probe(
    client: httpx.AsyncClient,
    target: Target,
    method: str,
    path: str,
    *,
    alias: str,
    start: datetime,
    end: datetime,
    root_alias: str | None,
) -> Probe:
    params, body = _body_for(target.body, alias=alias, start=start, end=end, root_alias=root_alias)
    try:
        response = await client.request(method, path, params=params, json=body)
    except httpx.HTTPError as exc:
        message = str(exc).strip()
        detail = type(exc).__name__ if not message else f"{type(exc).__name__}: {message}"
        return Probe(method, path, None, detail, None)
    if response.status_code in (401, 403):
        return Probe(method, path, response.status_code, None, None, "reachable, token rejected")
    try:
        payload: object = response.json()
    except ValueError:
        return Probe(method, path, response.status_code, None, None, "non-JSON body")
    if response.status_code >= 400:
        return Probe(method, path, response.status_code, None, None)
    return Probe(method, path, response.status_code, None, _row_count(payload))


async def _fetch_openapi(client: httpx.AsyncClient) -> tuple[str, object] | None:
    for path in OPENAPI_CANDIDATES:
        try:
            response = await client.get(path)
        except httpx.HTTPError:
            continue
        if response.status_code >= 400:
            continue
        try:
            return path, response.json()
        except ValueError:
            continue
    return None


def _openapi_operations(spec: object) -> list[tuple[str, str, str]]:
    if not isinstance(spec, Mapping):
        return []
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        return []
    operations: list[tuple[str, str, str]] = []
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
        for method in HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, Mapping):
                summary = str(operation.get("summary") or operation.get("operationId") or "")
                operations.append((method.upper(), str(path), summary))
    return operations


def _classify(path: str) -> str | None:
    low = path.lower()
    if "timeseries" in low or "history" in low:
        return "timeseries"
    if "event" in low:
        return "events"
    if "node" in low or "tree" in low or "model" in low:
        return "nodes"
    return None


def _discovered_by_target(
    operations: Sequence[tuple[str, str, str]],
) -> dict[str, list[tuple[str, str]]]:
    found: dict[str, list[tuple[str, str]]] = {"nodes": [], "timeseries": [], "events": []}
    for method, path, _summary in operations:
        kind = _classify(path)
        if kind and (method, path) not in found[kind]:
            found[kind].append((method, path))
    return found


def _candidates(
    target: Target, discovered: Mapping[str, Sequence[tuple[str, str]]]
) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = [target.configured]
    for candidate in list(discovered.get(target.key, ())) + list(target.fallbacks):
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered[:MAX_PROBES_PER_TARGET]


async def probe_targets(
    client: httpx.AsyncClient,
    targets: Sequence[Target],
    discovered: Mapping[str, Sequence[tuple[str, str]]],
    *,
    alias: str,
    start: datetime,
    end: datetime,
    root_alias: str | None,
) -> list[TargetResult]:
    results: list[TargetResult] = []
    for target in targets:
        result = TargetResult(target)
        for method, path in _candidates(target, discovered):
            result.probes.append(
                await _probe(
                    client,
                    target,
                    method,
                    path,
                    alias=alias,
                    start=start,
                    end=end,
                    root_alias=root_alias,
                )
            )
        results.append(result)
    return results


# --------------------------------------------------------------------------- #
# Adapter end-to-end check
# --------------------------------------------------------------------------- #


def _endpoints_from(results: Sequence[TargetResult]) -> SupOsEndpoints:
    defaults = SupOsEndpoints()
    chosen = {
        "nodes": defaults.nodes,
        "timeseries": defaults.timeseries,
        "current_values": defaults.current_values,
    }
    for result in results:
        best = result.best
        if best is not None:
            chosen[result.target.key] = best.path
    return SupOsEndpoints(
        nodes=chosen["nodes"],
        timeseries=chosen["timeseries"],
        current_values=chosen["current_values"],
    )


async def check_provider(
    *,
    base_url: str,
    token: str | None,
    timeout: float,
    verify_tls: bool,
    root_alias: str | None,
    endpoints: SupOsEndpoints,
    start: datetime,
    end: datetime,
) -> dict[str, object]:
    """Drive the real adapter against the best candidate paths and report what parsed."""
    client = HttpSupOsClient(
        base_url=base_url,
        token=token,
        endpoints=endpoints,
        timeout=timeout,
        verify_tls=verify_tls,
    )
    provider = SupOsProvider(client=client, uns_root_alias=root_alias)
    report: dict[str, object] = {}
    try:
        factories = await provider.get_factories()
        report["factories"] = len(factories)
        if not factories:
            report["note"] = "no factory node found — cannot chain the hierarchy"
            return report
        lines = await provider.get_lines(factories[0].factory_id)
        report["lines"] = len(lines)
        if not lines:
            return report
        stations = await provider.get_stations(lines[0].line_id)
        report["stations"] = len(stations)
        if not stations:
            return report
        station = stations[0]
        report["equipment"] = len(await provider.get_equipment(station.station_id))
        report["processes"] = len(await provider.get_processes(station.station_id))
        report["sample_station"] = station.station_id

        from app.domain.models import MetricKey  # local: only needed for the probe

        for metric in (MetricKey.FPY, MetricKey.CYCLE_TIME):
            points = await provider.get_metrics(
                metric=metric, start=start, end=end, station_id=station.station_id
            )
            report[f"metrics.{metric.value}"] = len(points)
        try:
            events = await provider.get_events(
                start=start, end=end, station_id=station.station_id
            )
            report["events"] = len(events)
        except SupOsUnavailableError:
            # Expected: supOS CE has no REST event endpoint. The MQTT probe
            # below is what actually validates event ingestion.
            report["events"] = "n/a — REST events unsupported (use MQTT)"
    except SupOsError as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        await client.aclose()
    return report


# --------------------------------------------------------------------------- #
# MQTT probe
# --------------------------------------------------------------------------- #


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


async def probe_mqtt(
    settings: MqttEventSettings, *, duration: float, max_messages: int
) -> MqttProbe:
    result = MqttProbe()
    try:
        import aiomqtt
    except ImportError:
        result.error = "aiomqtt is not installed (pip install aiomqtt>=2.3)"
        return result

    tls_context = ssl.create_default_context() if settings.tls else None
    loop = asyncio.get_running_loop()
    try:
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
            result.connected = True
            deadline = loop.time() + duration
            messages = client.messages.__aiter__()
            while result.message_count < max_messages:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    message = await asyncio.wait_for(messages.__anext__(), timeout=remaining)
                except (TimeoutError, StopAsyncIteration):
                    break
                topic = str(message.topic)
                payload = bytes(message.payload)
                result.message_count += 1
                _bump(result.topics, topic)
                _record_payload(result, topic, payload)
    except Exception as exc:  # broker unreachable, auth failure, protocol error
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _record_payload(result: MqttProbe, topic: str, payload: bytes) -> None:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        result.unparsed.append(f"{topic}: payload is not JSON")
        return
    if isinstance(decoded, Mapping):
        _bump(result.payload_keys, ",".join(sorted(str(key) for key in decoded)))
    try:
        record = parse_event_payload(topic, payload)
    except SupOsError as exc:
        result.unparsed.append(f"{topic}: {exc}")
        return
    result.parsed += 1
    _bump(result.event_types, record.event_type or "(empty)")
    _bump(result.severities, record.severity or "(empty)")
    result.samples.append((topic, payload.decode("utf-8", "replace")))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _tag(state: str) -> str:
    return f"[{state:<4}]"


def _print_probe(probe: Probe, *, marker: str = "") -> None:
    if probe.error is not None:
        detail = probe.error
    elif probe.status is None:
        detail = "no response"
    else:
        detail = f"HTTP {probe.status}"
        if probe.rows is not None:
            detail += f"  {probe.rows} rows"
        if probe.note:
            detail += f"  ({probe.note})"
    print(f"    {_tag(probe.state)} {probe.method:<4} {probe.path:<58} {detail}{marker}")


def render_discovery(
    openapi: tuple[str, object] | None,
    operations: Sequence[tuple[str, str, str]],
) -> None:
    print("[1] Gateway reachability & OpenAPI discovery")
    if openapi is None:
        print("    [WARN] OpenAPI document not exposed; falling back to candidate paths")
        return
    path, spec = openapi
    version = ""
    if isinstance(spec, Mapping):
        info = spec.get("info")
        if isinstance(info, Mapping):
            version = f"  ({info.get('title', '')} {info.get('version', '')})".rstrip()
    print(f"    [OK  ] GET  {path:<58} {len(operations)} operations{version}")
    relevant = [op for op in operations if _classify(op[1]) is not None]
    print(f"    UNS-related operations: {len(relevant)}")
    for method, op_path, summary in relevant[:40]:
        print(f"      {method:<5} {op_path:<62} {summary}")


def render_targets(
    results: Sequence[TargetResult], *, alias: str, placeholder: bool
) -> None:
    print()
    print("[2] Endpoint probe")
    if placeholder:
        print(f"    note: no --alias supplied; timeseries probed with placeholder {alias!r}")
    for result in results:
        target = result.target
        print(f"    -- {target.label} (configured {target.configured[0]} {target.configured[1]})")
        for probe in result.probes:
            marker = ""
            if (probe.method, probe.path) == target.configured:
                marker = "  <- configured"
            elif probe is result.best:
                marker = "  <- best alternative"
            _print_probe(probe, marker=marker)


def render_provider(report: Mapping[str, object], endpoints: SupOsEndpoints) -> None:
    print()
    print("[3] Adapter end-to-end parse (real SupOsProvider on the best candidate paths)")
    if "error" in report:
        print(f"    [FAIL] {report['error']}")
    else:
        for key in (
            "factories",
            "lines",
            "stations",
            "equipment",
            "processes",
            "metrics.fpy",
            "metrics.cycleTime",
            "events",
        ):
            if key in report:
                print(f"    [OK  ] {key:<18} {report[key]}")
        if "note" in report:
            print(f"    [WARN] {report['note']}")
    print("    SupOsEndpoints suggestion:")
    print(f"        SupOsEndpoints(nodes={endpoints.nodes!r},")
    print(f"                      timeseries={endpoints.timeseries!r},")
    print(f"                      current_values={endpoints.current_values!r})")


def render_mqtt(result: MqttProbe, *, show_payloads: bool) -> None:
    print()
    print("[4] MQTT event stream")
    if result.error is not None:
        print(f"    [FAIL] {result.error}")
        return
    if not result.connected:
        print("    [SKIP] no MQTT URL supplied (--mqtt-url / SUPOS_MQTT_URL)")
        return
    print(f"    [OK  ] connected; received {result.message_count} messages")
    for topic, count in sorted(result.topics.items()):
        print(f"      topic {topic}  x{count}")
    for keys, count in sorted(result.payload_keys.items()):
        print(f"      payload keys {{{keys}}}  x{count}")
    print(f"    parse_event_payload: {result.parsed} parsed, {len(result.unparsed)} dropped")
    for reason in result.unparsed[:5]:
        print(f"      [WARN] {reason}")
    if result.event_types:
        print(f"    event types seen: {result.event_types}")
    if result.severities:
        print(f"    severities seen:  {result.severities}")
    if show_payloads:
        for topic, payload in result.samples[:3]:
            print(f"      sample {topic}: {payload[:200]}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    settings = load_settings()
    parser = argparse.ArgumentParser(
        description="Verify the open supOS integration against a live deployment.",
    )
    parser.add_argument("--base-url", default=settings.sup_os_api_url)
    parser.add_argument("--token", default=settings.sup_os_api_token)
    parser.add_argument("--timeout", type=float, default=settings.sup_os_timeout_seconds)
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=not settings.sup_os_verify_tls,
        help="skip TLS verification (self-signed gateways)",
    )
    parser.add_argument("--root-alias", default=settings.sup_os_uns_root_alias)
    parser.add_argument("--alias", default=None, help="timeseries alias to probe")
    parser.add_argument("--window-hours", type=float, default=24.0)
    parser.add_argument("--mqtt-url", default=settings.sup_os_mqtt_url)
    parser.add_argument("--mqtt-topic", default=settings.sup_os_mqtt_topic)
    parser.add_argument("--mqtt-duration", type=float, default=20.0)
    parser.add_argument("--mqtt-max-messages", type=int, default=50)
    parser.add_argument("--skip-mqtt", action="store_true")
    parser.add_argument("--show-payloads", action="store_true")
    parser.add_argument("--json", dest="json_path", default=None)
    return parser.parse_args(argv)


def _exit_code(results: Sequence[TargetResult]) -> int:
    for result in results:
        probe = result.configured_probe
        if probe is not None and probe.state == "FAIL":
            return 1
    return 0


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    base_url = args.base_url
    if not base_url:
        print("ERROR: no gateway URL. Pass --base-url or set SUPOS_API_URL.", file=sys.stderr)
        return 2
    verify_tls = not args.insecure
    end = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    start = end - timedelta(hours=args.window_hours)

    print("=" * 78)
    print(" open supOS integration verification")
    print(f" gateway : {base_url}")
    print(f" auth    : {'bearer token set' if args.token else 'NO TOKEN (expect 401/403)'}")
    print(f" tls     : {'verify' if verify_tls else 'verify DISABLED'}")
    print(f" window  : {start.isoformat()} .. {end.isoformat()} ({args.window_hours}h)")
    print("=" * 78)

    headers = {"Accept": "application/json"}
    if args.token:
        headers["Cookie"] = f"supos_community_token={args.token}"

    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=args.timeout,
        verify=verify_tls,
        headers=headers,
    ) as client:
        openapi = await _fetch_openapi(client)
        operations = _openapi_operations(openapi[1]) if openapi else []
        discovered = _discovered_by_target(operations)
        render_discovery(openapi, operations)

        # A timeseries probe needs a concrete alias; without the nodes we cannot
        # know one, so an unset alias is a labelled placeholder and may return
        # zero rows even though the endpoint itself is healthy.
        alias = args.alias or "ST-A40.fpy"

        targets = build_targets()
        results = await probe_targets(
            client,
            targets,
            discovered,
            alias=alias,
            start=start,
            end=end,
            root_alias=args.root_alias,
        )
        render_targets(results, alias=alias, placeholder=args.alias is None)

        endpoints = _endpoints_from(results)
        provider_report = await check_provider(
            base_url=base_url,
            token=args.token,
            timeout=args.timeout,
            verify_tls=verify_tls,
            root_alias=args.root_alias,
            endpoints=endpoints,
            start=start,
            end=end,
        )
        render_provider(provider_report, endpoints)

        mqtt_result: MqttProbe | None = None
        if not args.skip_mqtt and args.mqtt_url:
            mqtt_settings = MqttEventSettings.from_url(
                args.mqtt_url,
                topic=args.mqtt_topic,
                username=settings.sup_os_mqtt_username,
                password=settings.sup_os_mqtt_password,
            )
            mqtt_result = await probe_mqtt(
                mqtt_settings, duration=args.mqtt_duration, max_messages=args.mqtt_max_messages
            )
        else:
            mqtt_result = MqttProbe(connected=False)
        render_mqtt(mqtt_result, show_payloads=args.show_payloads)

    code = _exit_code(results)
    print()
    print("=" * 78)
    outcome = "configured endpoints verified" if code == 0 else "configured endpoint(s) failed"
    print(f" result: exit {code} ({outcome})")
    print("=" * 78)

    if args.json_path:
        payload = {
            "gateway": base_url,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "openapi": None
            if openapi is None
            else {"path": openapi[0], "operations": len(operations)},
            "targets": [
                {
                    "key": result.target.key,
                    "configured": list(result.target.configured),
                    "probes": [
                        {
                            "method": probe.method,
                            "path": probe.path,
                            "status": probe.status,
                            "rows": probe.rows,
                            "state": probe.state,
                            "note": probe.note,
                            "error": probe.error,
                        }
                        for probe in result.probes
                    ],
                }
                for result in results
            ],
            "endpoints_suggestion": {
                "nodes": endpoints.nodes,
                "timeseries": endpoints.timeseries,
                "current_values": endpoints.current_values,
            },
            "provider": provider_report,
            "mqtt": {
                "connected": mqtt_result.connected,
                "error": mqtt_result.error,
                "message_count": mqtt_result.message_count,
                "topics": mqtt_result.topics,
                "payload_keys": mqtt_result.payload_keys,
                "parsed": mqtt_result.parsed,
                "unparsed": mqtt_result.unparsed,
                "event_types": mqtt_result.event_types,
                "severities": mqtt_result.severities,
            },
        }
        Path(args.json_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f" JSON report written to {args.json_path} (may contain plant data — do not commit)")

    return code


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    return asyncio.run(_run(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())