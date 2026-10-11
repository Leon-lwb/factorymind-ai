"""Seed open supOS history so FactoryMind can read real time series.

Why this exists (open supOS CE `V1.5.0.0-M`, verified 2026-10-12):

* A UNS time-series file node is stored with `table_name = supos_timeserial_<type>`
  and `with_flags = 4` (`UnsFlagWithSave2DB`). Values pushed over MQTT **are**
  persisted — into the shared typed table `public.supos_timeserial_double`
  (columns `tag, "timeStamp", status, value`), keyed by the node id.
* The history query API (`POST /inter-api/supos/uns/file/history/timeseries/query`),
  however, resolves the physical table from the node alias when the UNS detail
  carries no explicit table, i.e. it runs `... FROM "ST_A10_FPY" ...` — a
  relation that never exists. Read and write therefore disagree, and every
  history call fails with `42P01 relation "<alias>" does not exist`.

This script reconciles the two sides **without patching the supOS backend**:

1. `views`     — for every time-series node, create a view named after the alias
                 in `public` that projects the shared table rows for that node's
                 `tag`, exposing the columns the read path expects
                 (`"timeStamp"`, `value`, `quality`).
2. `backfill`  — insert `--hours` of realistic, deterministic samples into the
                 shared table so the app's default 8-hour window is populated
                 immediately (no waiting for real time to elapse). Station A40
                 carries a torque-anomaly story line so the RCA demo has signal.
3. `feed`      — keep publishing live values over MQTT so the "supOS push" leg
                 of the demo is real and the window keeps growing.

All three steps are metadata/data operations on the platform's own TimescaleDB
and EMQX — no supOS image rebuild, no manual console work.

Usage
-----
    py -3 seed_supos_history.py views
    py -3 seed_supos_history.py backfill --hours 8
    py -3 seed_supos_history.py feed --interval 3

Auth/config overrides via env or flags: `--pg-container` (default `postgresql`),
`--mqtt` (default `192.168.1.65:1883`).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from datetime import UTC, datetime, timedelta

# metric suffix -> (baseline, amplitude, period_minutes)
METRIC_PROFILE: dict[str, tuple[float, float, float]] = {
    "FPY": (96.5, 1.6, 180.0),
    "NOK_RATE": (2.2, 0.9, 150.0),
    "TORQUE_NOK_RATE": (1.4, 0.7, 130.0),
    "CYCLE_TIME": (54.0, 4.0, 200.0),
    "TOOL_ERROR_RATE": (1.1, 0.6, 170.0),
}

# Station A40 = 关键扭矩拧紧: torque anomaly ramps up over the last 2 hours.
ANOMALY_STATION = "ST_A40"
ANOMALY_WINDOW_MIN = 120.0


def _metric_of(alias: str) -> str | None:
    for suffix in sorted(METRIC_PROFILE, key=len, reverse=True):
        if alias.endswith("_" + suffix):
            return suffix
    return None


def _station_of(alias: str) -> str:
    metric = _metric_of(alias)
    return alias[: -(len(metric) + 1)] if metric else alias


def _sample(alias: str, minutes_ago: float) -> float:
    """Deterministic synthetic sample: seasonal baseline + A40 torque anomaly."""
    metric = _metric_of(alias)
    if metric is None:
        return 0.0
    base, amplitude, period = METRIC_PROFILE[metric]
    phase = sum(ord(ch) for ch in alias) % 360
    value = base + amplitude * math.sin(2 * math.pi * minutes_ago / period + phase)

    if _station_of(alias) == ANOMALY_STATION and minutes_ago <= ANOMALY_WINDOW_MIN:
        ramp = (ANOMALY_WINDOW_MIN - minutes_ago) / ANOMALY_WINDOW_MIN  # 0 → 1
        if metric == "TORQUE_NOK_RATE":
            value += 9.0 * ramp
        elif metric == "NOK_RATE":
            value += 5.5 * ramp
        elif metric == "FPY":
            value -= 6.0 * ramp
        elif metric == "CYCLE_TIME":
            value += 7.0 * ramp
    return round(max(value, 0.0), 4)


def _psql(container: str, sql: str, database: str = "postgres") -> str:
    proc = subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-v", "ON_ERROR_STOP=1",
         "-U", "postgres", "-d", database, "-t", "-A", "-f", "-"],
        input=sql, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
    return proc.stdout


def _nodes(container: str) -> list[tuple[int, str]]:
    sql = (
        "select id, alias from supos.uns_namespace "
        "where path_type=2 and data_type=1 and status=1 order by id;"
    )
    rows = [line for line in _psql(container, sql).splitlines() if "|" in line]
    return [(int(rid), alias) for rid, alias in (row.split("|", 1) for row in rows)]


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def cmd_views(container: str) -> int:
    nodes = _nodes(container)
    stmts = [
        "-- reconcile history read/write: alias view over the shared typed table",
        "begin;",
    ]
    for node_id, alias in nodes:
        stmts.append(f'drop table if exists public.{_quote(alias)} cascade;')
        stmts.append(
            f'create view public.{_quote(alias)} as '
            f'select "timeStamp", value, coalesce(status, 0) as quality '
            f'from public.supos_timeserial_double where tag = {node_id};'
        )
    stmts.append("commit;")
    _psql(container, "\n".join(stmts))
    print(f"views: created {len(nodes)} alias views in public")
    return 0


def cmd_backfill(container: str, hours: float) -> int:
    nodes = _nodes(container)
    now = datetime.now(UTC).replace(microsecond=0)
    steps = int(hours * 60)
    rows: list[str] = []
    for node_id, alias in nodes:
        for i in range(steps, -1, -1):
            ts = (now - timedelta(minutes=i)).isoformat()
            value = _sample(alias, float(i))
            rows.append(f"({node_id}, '{ts}', 0, {value})")

    cutoff = (now - timedelta(hours=hours)).isoformat()
    stmts = [
        "-- backfill shared history table with deterministic samples",
        "begin;",
        'delete from public.supos_timeserial_double '
        f'where "timeStamp" >= \'{cutoff}\'::timestamptz;',
    ]
    # Insert in chunks to keep statement size reasonable.
    chunk = 4000
    for start in range(0, len(rows), chunk):
        stmts.append(
            'insert into public.supos_timeserial_double(tag, "timeStamp", status, value) values '
            + ",".join(rows[start:start + chunk])
            + ' on conflict do nothing;'
        )
    stmts.append("commit;")
    stmts.append(
        'select count(*), min("timeStamp"), max("timeStamp") '
        "from public.supos_timeserial_double;"
    )
    out = _psql(container, "\n".join(stmts))
    print(f"backfill: {len(nodes)} nodes x {steps + 1} samples over {hours}h")
    print("rows:", out.strip().splitlines()[-1] if out.strip() else "?")
    return 0


def cmd_feed(host: str, interval: float) -> int:
    import paho.mqtt.client as mqtt  # imported lazily; only the feeder needs it

    container = "postgresql"
    nodes = _nodes(container)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="factorymind-feeder")
    client.connect(host, 1883, 30)
    client.loop_start()
    print(f"feed: publishing live values for {len(nodes)} nodes every {interval}s -> {host}:1883")
    try:
        while True:
            stamp = datetime.now(UTC)
            for _, alias in nodes:
                payload = {"value": _sample(alias, 0.0), "timeStamp": stamp.isoformat()}
                client.publish(alias, json.dumps(payload), qos=1)
            client.loop()  # let the network loop breathe
            import time as _time
            _time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["views", "backfill", "feed"])
    parser.add_argument("--pg-container", default="postgresql")
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--mqtt", default="192.168.1.65")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()

    if args.command == "views":
        return cmd_views(args.pg_container)
    if args.command == "backfill":
        return cmd_backfill(args.pg_container, args.hours)
    return cmd_feed(args.mqtt, args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
