"""Seed the FactoryMind UNS model into a live open supOS instance (spec §22).

`config/uns_model.json` is the single source of truth for the assembly-line
quality model: the Factory → Line → Station → Equipment/Process folder tree and
the per-station time-series files that carry FPY / NOK / torque / cycle time /
tool-error-rate. This script turns that model into the payload the supOS UNS API
expects and, on request, pushes it.

Endpoint and payload shape are taken from the open supOS `V1.5.0.0-M` backend
source, not guessed — `POST /inter-api/supos/uns/batch` with a body of
`[]BatchCreateTopicRequest` (`internal/domain/uns/types.go`,
`internal/interfaces/http/router.go`). See `docs/UNS_MODEL.md`.

Two rules:

* **Dry run by default.** Without `--apply` the script only prints the payload
  and a summary; nothing is written to supOS.
* **Never fabricate a pass.** A rejected node is reported with supOS's own error
  message and the exit code is non-zero. The script does not retry into a
  partial, unreported state.

Run:
    python scripts/seed_uns.py
    python scripts/seed_uns.py --apply --base-url http://<host>:18088 --token <access_token>
    python scripts/seed_uns.py --apply --verify

Environment fallbacks: SUPOS_API_URL, SUPOS_API_TOKEN, SUPOS_TIMEOUT_SECONDS,
SUPOS_VERIFY_TLS.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "config" / "uns_model.json"
BATCH_PATH = "/inter-api/supos/uns/batch"
TREE_PATH = "/inter-api/supos/uns/tree"

PATH_TYPE_DIR = 0
PATH_TYPE_FILE = 2

# Node kinds are carried in `extendProperties.factorymindType`. This is required,
# not cosmetic: `/uns/tree` returns `type` mirroring `pathType` (0/2), so the
# adapter cannot tell a line from a station by wire type alone.
KIND_KEY = "factorymindType"


class SeedError(RuntimeError):
    """The model is unusable or supOS rejected it."""


# supOS validates a node's `name` against ^[\p{Han}A-Za-z0-9_-]+$ and rejects
# anything else with `uns.name.format.error` (verified against the running
# V1.5.0.0-M instance: it refuses spaces and `#`). The model keeps human-readable
# labels in `displayName`; here we derive an API-safe `name` from the same string.
_NAME_UNSAFE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9_-]+")
_NAME_MAX = 63


def _safe_name(value: str) -> str:
    safe = _NAME_UNSAFE.sub("_", value).strip("_")
    return safe[:_NAME_MAX] or "node"


@dataclass(frozen=True, slots=True)
class Node:
    """One UNS node, ready to serialise into a BatchCreateTopicRequest."""

    alias: str
    name: str
    display_name: str
    path_type: int
    parent_alias: str
    description: str = ""
    data_type: int | None = None
    definition: tuple[dict[str, Any], ...] = ()
    extend_properties: dict[str, Any] | None = None
    persistence: bool | None = None

    def to_request(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "alias": self.alias,
            "name": _safe_name(self.name),
            "displayName": self.display_name,
            "parentAlias": self.parent_alias,
            "pathType": self.path_type,
        }
        if self.description:
            body["description"] = self.description
        if self.data_type is not None:
            body["dataType"] = self.data_type
        if self.definition:
            body["definition"] = list(self.definition)
        if self.extend_properties:
            body["extendProperties"] = dict(self.extend_properties)
        if self.persistence is not None:
            body["persistence"] = self.persistence
        return body


def load_model(path: Path | None = None) -> dict[str, Any]:
    model_path = path or DEFAULT_MODEL_PATH
    try:
        model = json.loads(model_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeedError(f"UNS model not found: {model_path}") from exc
    except json.JSONDecodeError as exc:
        raise SeedError(f"UNS model is not valid JSON: {model_path}: {exc}") from exc
    if not isinstance(model, dict):
        raise SeedError("UNS model must be a JSON object")
    return model


def metric_alias(template: str, station_alias: str, slug: str) -> str:
    """Metric file alias, e.g. `{station}_{metric}` → `ST_A40_FPY`.

    Kept in lockstep with `SupOsProvider._metric_alias` — the adapter queries
    exactly these aliases, so the two must agree or reads return nothing.
    """
    return template.format(station=station_alias, metric=slug)


def build_nodes(model: dict[str, Any]) -> list[Node]:
    """Flatten the model into UNS nodes, parents before children.

    `POST /uns/batch` creates items in array order and rejects a node whose
    `parentAlias` does not yet exist, so the ordering is load-bearing.
    """
    root = model["root"]
    template = str(model["metricAliasTemplate"])
    metrics: dict[str, dict[str, Any]] = model["metrics"]
    station_metrics: list[str] = list(model["stationMetrics"])
    file_fields = tuple(dict(field) for field in model["fileFields"])

    nodes: list[Node] = [
        Node(
            alias=str(root["alias"]),
            name=str(root["name"]),
            display_name=str(root.get("displayName") or root["name"]),
            path_type=PATH_TYPE_DIR,
            parent_alias="",
            extend_properties=dict(root.get("extendProperties") or {}),
        )
    ]

    for line in model["lines"]:
        line_alias = str(line["alias"])
        nodes.append(
            Node(
                alias=line_alias,
                name=str(line["name"]),
                display_name=str(line.get("displayName") or line["name"]),
                path_type=PATH_TYPE_DIR,
                parent_alias=str(root["alias"]),
                extend_properties={KIND_KEY: "line"},
            )
        )
        for station in line["stations"]:
            station_alias = str(station["alias"])
            nodes.append(
                Node(
                    alias=station_alias,
                    name=str(station["name"]),
                    display_name=str(station.get("displayName") or station["name"]),
                    path_type=PATH_TYPE_DIR,
                    parent_alias=line_alias,
                    extend_properties={KIND_KEY: "station"},
                )
            )
            for equipment in station.get("equipment", []):
                nodes.append(
                    Node(
                        alias=str(equipment["alias"]),
                        name=str(equipment["name"]),
                        display_name=str(equipment.get("displayName") or equipment["name"]),
                        path_type=PATH_TYPE_DIR,
                        parent_alias=station_alias,
                        extend_properties={
                            KIND_KEY: "equipment",
                            "equipmentType": str(equipment["equipmentType"]),
                        },
                    )
                )
            for process in station.get("processes", []):
                nodes.append(
                    Node(
                        alias=str(process["alias"]),
                        name=str(process["name"]),
                        display_name=str(process.get("displayName") or process["name"]),
                        path_type=PATH_TYPE_DIR,
                        parent_alias=station_alias,
                        extend_properties={
                            KIND_KEY: "process",
                            "operation": str(process["operation"]),
                        },
                    )
                )
            for metric_key in station_metrics:
                metric = metrics[metric_key]
                nodes.append(
                    Node(
                        alias=metric_alias(template, station_alias, str(metric["slug"])),
                        name=f"{station['name']} {metric['displayName']}",
                        display_name=str(metric["displayName"]),
                        path_type=PATH_TYPE_FILE,
                        parent_alias=station_alias,
                        description=str(metric.get("description") or ""),
                        data_type=int(metric["dataType"]),
                        definition=file_fields,
                        persistence=True,
                    )
                )
    return nodes


def build_payload(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [node.to_request() for node in build_nodes(model)]


def event_topics(model: dict[str, Any]) -> list[str]:
    template = str(model["events"]["topicTemplate"])
    return [
        template.format(station=station["alias"])
        for line in model["lines"]
        for station in line["stations"]
    ]


def _alias_conflicts(payload: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, str] = {}
    conflicts: list[str] = []
    for node in payload:
        alias = node["alias"]
        if alias in seen:
            conflicts.append(alias)
        seen[alias] = alias
    return conflicts


def _missing_parents(payload: list[dict[str, Any]]) -> list[str]:
    """Parents that appear after their child, or not at all."""
    known: set[str] = set()
    missing: list[str] = []
    for node in payload:
        parent = node.get("parentAlias") or ""
        if parent and parent not in known:
            missing.append(f"{node['alias']} → {parent}")
        known.add(node["alias"])
    return missing


def _check_model(payload: list[dict[str, Any]]) -> None:
    conflicts = _alias_conflicts(payload)
    if conflicts:
        raise SeedError(f"duplicate aliases in model: {', '.join(sorted(set(conflicts)))}")
    missing = _missing_parents(payload)
    if missing:
        raise SeedError(
            "model lists a child before its parent (batch create would reject it): "
            + "; ".join(missing)
        )


def _summarise(payload: list[dict[str, Any]], model: dict[str, Any]) -> str:
    folders = sum(1 for node in payload if node["pathType"] == PATH_TYPE_DIR)
    files = sum(1 for node in payload if node["pathType"] == PATH_TYPE_FILE)
    stations = sum(
        1
        for line in model["lines"]
        for _ in line["stations"]
    )
    return (
        f"{len(payload)} nodes ({folders} folders, {files} time-series files) "
        f"across {len(model['lines'])} lines / {stations} stations"
    )


def _post_batch(
    client: httpx.Client, payload: list[dict[str, Any]]
) -> list[str]:
    """POST the batch and return supOS's per-index error messages (empty = ok)."""
    try:
        response = client.post(BATCH_PATH, params={"flags": 0}, json=payload)
    except httpx.HTTPError as exc:
        raise SeedError(f"POST {BATCH_PATH} failed: {exc}") from exc
    if response.status_code >= 400:
        raise SeedError(f"POST {BATCH_PATH} returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise SeedError(f"POST {BATCH_PATH} returned a non-JSON body") from exc

    errors: list[str] = []
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, dict):
            for index, message in data.items():
                if message:
                    errors.append(f"index {index}: {message}")
        code = body.get("code")
        if code not in (None, 0, 200):
            errors.append(f"code {code}: {body.get('msg')}")
    return errors


def _count_tree(client: httpx.Client) -> int | None:
    try:
        response = client.get(TREE_PATH, params={"pageSize": 1000})
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    data = body.get("data") if isinstance(body, dict) else None
    return len(data) if isinstance(data, list) else None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--base-url", default=os.getenv("SUPOS_API_URL"))
    parser.add_argument("--token", default=os.getenv("SUPOS_API_TOKEN"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually POST to supOS (default: dry run, print only)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="after --apply, read the UNS tree back and report the node count",
    )
    parser.add_argument("--json-out", type=Path, help="also write the payload here")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        model = load_model(args.model)
        payload = build_payload(model)
        _check_model(payload)
    except SeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"model: {args.model}")
    print(f"summary: {_summarise(payload, model)}")
    print("event topics:")
    for topic in event_topics(model):
        print(f"  {topic}")

    if args.json_out:
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"payload written: {args.json_out}")

    if not args.apply:
        print()
        print("DRY RUN — payload follows. Re-run with --apply to create it in supOS.")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not args.base_url:
        print("ERROR: --apply requires --base-url or SUPOS_API_URL", file=sys.stderr)
        return 2

    headers = {"Accept": "application/json"}
    if args.token:
        headers["Cookie"] = f"supos_community_token={args.token}"
    verify_tls = os.getenv("SUPOS_VERIFY_TLS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    timeout = float(os.getenv("SUPOS_TIMEOUT_SECONDS", "10"))

    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
        verify=verify_tls,
    ) as client:
        try:
            errors = _post_batch(client, payload)
        except SeedError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        if errors:
            print(f"REJECTED: {len(errors)} node(s) — supOS returned:", file=sys.stderr)
            for message in errors:
                print(f"  {message}", file=sys.stderr)
            return 1

        print(f"applied: {len(payload)} nodes created or updated")

        if args.verify:
            count = _count_tree(client)
            if count is None:
                print("WARN: could not read the UNS tree back to verify", file=sys.stderr)
                return 1
            print(f"verified: /uns/tree now reports {count} nodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
