"""Register the FactoryMind AI route on the open supOS gateway (spec §22).

FactoryMind AI deploys as a container beside supOS; the gateway needs a route
entry pointing at it before `/factorymind` resolves. The repository research
located the endpoint (`POST /inter-api/supos/app/routes/register`) but not its
request body — and this project does not guess platform APIs.

So this script inverts the problem: it *discovers* the operation from the
deployed instance's OpenAPI document, prints the request body schema the
instance actually declares, and registers using fields you supply explicitly.
Nothing is invented — if the schema says the field is `routeName`, you pass
`--field routeName=...`, not a value this script guessed.

Run:
    # 1. See what the instance expects (no request is sent):
    python scripts/register_supos_route.py --base-url http://<host>:8088 --token <bearer>

    # 2. Register with the fields the schema showed:
    python scripts/register_supos_route.py --base-url http://<host>:8088 --token <bearer> \\
        --field name=factorymind --field path=/factorymind \\
        --field target=http://factorymind-web:80 --apply

    # List every app/route operation the instance exposes:
    python scripts/register_supos_route.py --base-url ... --token ... --list

Environment fallbacks: SUPOS_API_URL, SUPOS_API_TOKEN, SUPOS_TIMEOUT_SECONDS,
SUPOS_VERIFY_TLS.

Exit codes: 0 success (including a dry run), 1 the request failed, 2 the
arguments are unusable, 3 the endpoint could not be discovered.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# Run as `python scripts/register_supos_route.py` puts scripts/ on sys.path, not
# the service root, so `app` would not resolve. Add the root explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from app.config import load_settings

OPENAPI_CANDIDATES = ("/v3/api-docs", "/v3/api-docs/", "/api-docs", "/openapi.json")

HTTP_METHODS = ("post", "put", "get", "patch", "delete")

# Candidates, not facts: the deployed instance's OpenAPI document is authoritative.
DEFAULT_REGISTER_PATH = "/inter-api/supos/app/routes/register"
DEFAULT_UNREGISTER_PATH = "/inter-api/supos/app/routes/unregister"

# An operation counts as the route-registration surface when its path carries
# both ideas. Kept narrow so unrelated "register" endpoints are not offered.
_REGISTER_TERMS = ("route", "register")


class RegistrationError(RuntimeError):
    """The route could not be registered, and the reason is reportable."""


def _client(args: argparse.Namespace) -> httpx.AsyncClient:
    settings = load_settings()
    base_url = (args.base_url or settings.sup_os_api_url or "").strip()
    if not base_url:
        raise RegistrationError("no gateway URL: pass --base-url or set SUPOS_API_URL")
    token = (args.token or settings.sup_os_api_token or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Cookie"] = f"supos_community_token={token}"
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/") + "/",
        headers=headers,
        timeout=args.timeout,
        verify=not args.insecure,
    )


async def _fetch_openapi(client: httpx.AsyncClient) -> Mapping[str, Any] | None:
    for candidate in OPENAPI_CANDIDATES:
        try:
            response = await client.get(candidate.lstrip("/"))
        except httpx.HTTPError:
            continue
        if response.status_code != 200:
            continue
        try:
            spec = response.json()
        except ValueError:
            continue
        if isinstance(spec, Mapping) and "paths" in spec:
            return spec
    return None


def _operations(spec: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        return []
    found: list[tuple[str, str, Mapping[str, Any]]] = []
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
        for method in HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, Mapping):
                found.append((method.upper(), str(path), operation))
    return found


def _registration_operations(
    spec: Mapping[str, Any],
) -> list[tuple[str, str, Mapping[str, Any]]]:
    matches: list[tuple[str, str, Mapping[str, Any]]] = []
    for method, path, operation in _operations(spec):
        lowered = path.lower()
        if all(term in lowered for term in _REGISTER_TERMS):
            matches.append((method, path, operation))
    return matches


def _resolve_ref(spec: Mapping[str, Any], schema: object) -> Mapping[str, Any]:
    """Follow `$ref` links into `components` until a concrete schema is reached."""
    node = schema
    for _ in range(16):
        if not isinstance(node, Mapping):
            return {}
        ref = node.get("$ref")
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return node
        target: Any = spec
        for part in ref[2:].split("/"):
            target = target.get(part) if isinstance(target, Mapping) else None
        node = target
    return node if isinstance(node, Mapping) else {}


def _body_schema(spec: Mapping[str, Any], operation: Mapping[str, Any]) -> Mapping[str, Any]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, Mapping):
        return {}
    content = request_body.get("content")
    if not isinstance(content, Mapping):
        return {}
    for media in ("application/json", *content.keys()):
        entry = content.get(media)
        if isinstance(entry, Mapping):
            return _resolve_ref(spec, entry.get("schema"))
    return {}


def _describe_schema(schema: Mapping[str, Any]) -> str:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return "    （该操作未声明请求体字段，请以实例 Swagger 为准）"
    required = set(schema.get("required") or [])
    lines: list[str] = []
    for name, raw in properties.items():
        field = _resolve_ref(schema, raw)
        kind = field.get("type") or ("object" if "properties" in field else "?")
        mark = "必填" if name in required else "可选"
        description = field.get("description") or ""
        suffix = f" — {description}" if description else ""
        lines.append(f"    - {name}: {kind}（{mark}）{suffix}")
    return "\n".join(lines)


def _coerce(raw: str) -> Any:
    """Accept JSON scalars/objects so `enabled=true` becomes a bool, not a string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _body_from_fields(fields: Sequence[str]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for item in fields:
        key, separator, value = item.partition("=")
        key = key.strip()
        if not separator or not key:
            raise RegistrationError(f"--field expects key=value, got {item!r}")
        body[key] = _coerce(value)
    return body


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register (or unregister) the FactoryMind AI route on the supOS gateway.",
    )
    parser.add_argument("--base-url", help="gateway base URL (default: SUPOS_API_URL)")
    parser.add_argument(
        "--token",
        help="auth token, sent as the supos_community_token cookie (default: SUPOS_API_TOKEN)",
    )
    parser.add_argument("--path", help="override the route path (default: discovered)")
    parser.add_argument(
        "--method",
        default="POST",
        help="HTTP method for the registration call (default: POST)",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help="request body field as key=value; repeat for each field",
    )
    parser.add_argument(
        "--json-body",
        type=Path,
        help="read the request body from a JSON file instead of --field",
    )
    parser.add_argument(
        "--unregister",
        action="store_true",
        help="target the unregister path instead of the register path",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the app/route operations the instance exposes, then exit",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually send the request (default: print the plan only)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS verification (self-signed gateway certificates)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="request timeout in seconds (default: SUPOS_TIMEOUT_SECONDS)",
    )
    args = parser.parse_args(argv)
    if args.timeout is None:
        args.timeout = load_settings().sup_os_timeout_seconds
    return args


def _build_body(args: argparse.Namespace) -> dict[str, Any]:
    if args.json_body is not None:
        try:
            raw = json.loads(args.json_body.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RegistrationError(f"cannot read --json-body {args.json_body}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise RegistrationError("--json-body must contain a JSON object")
        return dict(raw)
    return _body_from_fields(args.field)


async def _run(args: argparse.Namespace) -> int:
    async with _client(args) as client:
        spec = await _fetch_openapi(client)

        if args.list:
            if spec is None:
                print("无法读取 OpenAPI 文档；请改用 --path 显式指定路径。", file=sys.stderr)
                return 3
            operations = _registration_operations(spec)
            if not operations:
                print("实例未暴露任何 route/register 操作。", file=sys.stderr)
                return 3
            print("实例暴露的 route/register 操作：")
            for method, path, operation in operations:
                summary = operation.get("summary") or operation.get("operationId") or ""
                print(f"  {method:5s} {path}  {summary}")
            return 0

        target = args.path
        if not target and spec is not None:
            matches = _registration_operations(spec)
            wanted = "unregister" if args.unregister else "register"
            preferred = [item for item in matches if wanted in item[1].lower()]
            chosen = preferred or matches
            if chosen:
                target = chosen[0][1]
                print(f"OpenAPI 发现的候选路径：{chosen[0][0]} {chosen[0][1]}")
        if not target:
            target = DEFAULT_UNREGISTER_PATH if args.unregister else DEFAULT_REGISTER_PATH
            print(f"未发现 OpenAPI；使用候选路径 {target}（未经实例核验）。")

        schema: Mapping[str, Any] = {}
        if spec is not None:
            for method, path, operation in _operations(spec):
                if path == target and method == args.method.upper():
                    schema = _body_schema(spec, operation)
                    break
        if schema:
            print("该操作声明的请求体字段（请据此提供 --field）：")
            print(_describe_schema(schema))

        body = _build_body(args)
        if not body:
            print(
                "请求体为空。请先按上方 schema 用 --field key=value 提供字段，"
                "或用 --json-body 提供完整 JSON；本脚本不会替你猜测字段。",
                file=sys.stderr,
            )
            return 2

        plan = {
            "method": args.method.upper(),
            "path": target,
            "body": body,
        }
        print("将要发送：")
        print(json.dumps(plan, ensure_ascii=False, indent=2))

        if not args.apply:
            print("\n（dry-run：未发送请求。确认无误后加 --apply。）")
            return 0

        try:
            response = await client.request(args.method.upper(), target.lstrip("/"), json=body)
        except httpx.HTTPError as exc:
            raise RegistrationError(f"请求失败：{type(exc).__name__}: {exc}") from exc

        print(f"\nHTTP {response.status_code}")
        print(response.text[:2000])
        if 200 <= response.status_code < 300:
            print("\n路由注册成功。")
            return 0
        print("\n路由注册失败，请核对字段与网关状态。", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except RegistrationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())