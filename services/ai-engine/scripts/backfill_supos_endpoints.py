"""Backfill verified supOS endpoint paths into `SupOsEndpoints` defaults (spec §22).

`scripts/verify_supos.py --json` records which paths a live deployment actually
served. This tool turns that report into a source edit: it rewrites the default
field values of `SupOsEndpoints` in `app/providers/supos.py` so the adapter stops
carrying the candidate paths and starts carrying the verified ones.

It is deliberately conservative:

* **Only verified paths are written.** A target is backfilled only when a probe
  reached HTTP 2xx *and* the adapter's own envelope parser extracted rows
  (`state == "OK"`). A path that merely responded 2xx but could not be parsed
  (WARN) is left alone unless `--allow-warn` is passed — a path that does not
  parse is not verified, and silently adopting it would bake a guess into code.
* **Dry-run by default.** Nothing is written without `--write`; the pending diff
  is printed so the change can be reviewed first.
* **Never writes broken Python.** The rewritten module is parsed with `ast`
  before it touches disk; a syntax error aborts the write.
* **`--check` for CI.** Compare the report against the current defaults and exit
  3 on drift, so a verified path cannot silently fall back out of the code.

Run:
    python scripts/verify_supos.py --base-url http://<host>:8088 --token <t> --json report.json
    python scripts/backfill_supos_endpoints.py --report report.json          # show the diff
    python scripts/backfill_supos_endpoints.py --report report.json --write   # apply it
    python scripts/backfill_supos_endpoints.py --report report.json --check   # CI: fail on drift

After writing, run `python -m pytest -q` and update the §3 table in
`docs/SUPOS_INTEGRATION.md` to match.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = SERVICE_ROOT / "app" / "providers" / "supos.py"

CLASS_NAME = "SupOsEndpoints"

# Report target key -> dataclass field name.
FIELD_BY_TARGET: dict[str, str] = {
    "nodes": "nodes",
    "timeseries": "timeseries",
    "current_values": "current_values",
}

ACCEPTED_STATES = {"OK"}


# --------------------------------------------------------------------------- #
# Report reading
# --------------------------------------------------------------------------- #


class BackfillError(RuntimeError):
    """The report or the source file could not be used."""


def load_report(path: Path) -> Mapping[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BackfillError(f"cannot read report {path}: {exc}") from exc
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BackfillError(f"report {path} is not valid JSON: {exc}") from exc
    if not isinstance(report, Mapping):
        raise BackfillError(f"report {path} is not a JSON object")
    return report


def select_paths(
    report: Mapping[str, object], *, accepted_states: set[str]
) -> tuple[dict[str, str], list[str]]:
    """Pick the verified path per target from the probe list.

    Returns `(chosen, notes)`: `chosen` maps a dataclass field to a verified path;
    `notes` explains every target that was skipped and why.
    """
    targets = report.get("targets")
    if not isinstance(targets, list):
        raise BackfillError(
            "report has no 'targets' list — regenerate it with verify_supos.py --json"
        )

    chosen: dict[str, str] = {}
    notes: list[str] = []
    for entry in targets:
        if not isinstance(entry, Mapping):
            continue
        key = str(entry.get("key") or "")
        field = FIELD_BY_TARGET.get(key)
        if field is None:
            continue
        probes = entry.get("probes")
        if not isinstance(probes, list):
            notes.append(f"{key}: no probes recorded")
            continue
        picked: Mapping[str, object] | None = None
        rejected: list[str] = []
        for probe in probes:
            if not isinstance(probe, Mapping):
                continue
            state = str(probe.get("state") or "")
            if state in accepted_states:
                picked = probe
                break
            if state == "WARN":
                rejected.append(str(probe.get("path") or ""))
        if picked is None:
            detail = f" (unparsed 2xx candidate: {', '.join(rejected)})" if rejected else ""
            notes.append(f"{key}: no verified path{detail} — left unchanged")
            continue
        path = str(picked.get("path") or "")
        if not _valid_path(path):
            notes.append(f"{key}: verified path {path!r} is not a usable path — left unchanged")
            continue
        chosen[field] = path
    return chosen, notes


def _valid_path(path: str) -> bool:
    return path.startswith("/") and not any(ch in path for ch in ' \t"\'\\')


# --------------------------------------------------------------------------- #
# Source rewriting
# --------------------------------------------------------------------------- #


def class_block(source: str, name: str) -> tuple[int, int]:
    """Return the `[start, end)` slice of the top-level class `name`."""
    match = re.search(rf"^class {re.escape(name)}\b", source, re.MULTILINE)
    if match is None:
        raise BackfillError(f"class {name} not found in the source file")
    rest = source[match.end() :]
    following = re.search(r"^(?:class |def |@)", rest, re.MULTILINE)
    end = match.end() + (following.start() if following else len(rest))
    return match.start(), end


def apply_defaults(source: str, paths: Mapping[str, str]) -> tuple[str, list[str]]:
    """Rewrite the dataclass defaults for the given fields.

    Returns `(new_source, changed_fields)`. A field whose default already equals
    the verified path is reported as unchanged, so the tool is idempotent.
    """
    start, end = class_block(source, CLASS_NAME)
    block = source[start:end]
    changed: list[str] = []
    for field, value in paths.items():
        pattern = re.compile(rf'^(\s*{re.escape(field)}:\s*str\s*=\s*)"[^"]*"', re.MULTILINE)
        match = pattern.search(block)
        if match is None:
            raise BackfillError(f"field {field!r} not found in class {CLASS_NAME}")
        if match.group(0).endswith(f'"{value}"'):
            continue
        block = pattern.sub(lambda m, v=value: f'{m.group(1)}"{v}"', block, count=1)
        changed.append(field)
    return source[:start] + block + source[end:], changed


def _render_diff(before: str, after: str, label: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{label} (current)",
        tofile=f"{label} (backfilled)",
    )
    return "".join(diff)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill verified supOS endpoint paths into SupOsEndpoints defaults.",
    )
    parser.add_argument("--report", required=True, help="verify_supos.py --json output")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="file holding SupOsEndpoints")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="apply the change (default: dry-run)")
    mode.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 3 if the defaults drift from the verified report",
    )
    parser.add_argument(
        "--allow-warn",
        action="store_true",
        help="also backfill 2xx-but-unparsed paths (not recommended)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    report_path = Path(args.report)
    source_path = Path(args.source)

    try:
        report = load_report(report_path)
        accepted = {"OK", "WARN"} if args.allow_warn else set(ACCEPTED_STATES)
        chosen, notes = select_paths(report, accepted_states=accepted)
    except BackfillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"report : {report_path}")
    print(f"source : {source_path}")
    for key, value in chosen.items():
        print(f"  verified {key:<11} {value}")
    for note in notes:
        print(f"  [skip] {note}")

    if not chosen:
        print()
        print("nothing to backfill: no endpoint was verified. The defaults are unchanged.")
        return 2

    try:
        before = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read {source_path}: {exc}", file=sys.stderr)
        return 1

    try:
        after, changed = apply_defaults(before, chosen)
    except BackfillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not changed:
        print()
        print("already up to date: every verified path already matches the defaults.")
        return 0

    print()
    print(_render_diff(before, after, str(source_path)), end="")
    print()

    if args.check:
        fields = ", ".join(changed)
        print(f"DRIFT: {len(changed)} field(s) differ from the verified report ({fields}).")
        print("backfill with: --write (drop --check)")
        return 3

    try:
        ast.parse(after)
    except SyntaxError as exc:
        print(f"ERROR: rewrite would produce invalid Python, aborting: {exc}", file=sys.stderr)
        return 1

    if not args.write:
        print(f"dry-run: {len(changed)} field(s) would change ({', '.join(changed)}).")
        print("re-run with --write to apply.")
        return 0

    try:
        source_path.write_text(after, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot write {source_path}: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(changed)} field(s) to {source_path} ({', '.join(changed)}).")
    print("next: python -m pytest -q  ·  update the §3 table in docs/SUPOS_INTEGRATION.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())