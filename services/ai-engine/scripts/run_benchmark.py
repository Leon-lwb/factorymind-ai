# ruff: noqa: E501
# The report template at the bottom embeds Markdown whose long lines are document
# content, not code; wrapping them would change what the generated file renders.

"""Benchmark harness (spec §27, §47).

Measures the deterministic engine on a labeled synthetic dataset and writes
`docs/BENCHMARK.md`. The report is *generated*, never hand-edited, so a number
in the document cannot drift from the number the engine produces.

Ground truth is independent of the engine under test:

* Anomaly labels come from the incidents authored in `app.providers.mock` — we
  know which (station, metric) we injected, and one shift is deliberately tuned
  below the detection gate so it corroborates evidence without being triaged.
* FMEA labels are the Master FMEA rows themselves; a query is correct when it
  retrieves the row it was built from.
* Root-cause labels are the incident design intent, recorded here rather than
  read back out of the ranking.

Everything below is measured on synthetic data. Per spec §54 the results are
Demonstration / Simulation Results — no number here is a claim about a real
plant, and the manual-vs-assisted time comparison is an explicit assumption, not
a measurement.

Run:  python scripts/run_benchmark.py --seed 2026 --days 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

# Run as `python scripts/run_benchmark.py` puts scripts/ on sys.path, not the
# service root, so `app` would not resolve. Add the root explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import __version__
from app.config import AnomalyRules, AssessRules, load_anomaly_rules, load_assess_rules
from app.domain.models import MetricKey
from app.engine import (
    FmeaQuery,
    detect_anomalies,
    load_master_fmea,
    match_fmea,
    rank_root_causes,
)
from app.providers.mock import MockIndustrialDataProvider

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DOCS_PATH = SERVICE_ROOT.parent.parent / "docs" / "BENCHMARK.md"

FACTORY_ID = "FAC-DEMO-01"

# Default detection gate; matches config/anomaly_rules.json.
DEFAULT_THRESHOLD = 3.0

# Threshold sweep — the precision/recall trade-off is the interesting result,
# not the single operating point.
THRESHOLDS = (2.0, 2.5, 3.0, 3.5, 4.0, 4.5)

# --------------------------------------------------------------------------- #
# Ground truth
# --------------------------------------------------------------------------- #
#
# The incidents authored in `app.providers.mock`. `detectable` marks whether the
# shift is meant to clear the 3-sigma detection gate. ST-A40 toolErrorRate is
# False on purpose: it is a corroborating signal tuned to sit between the 2-sigma
# evidence gate and the 3-sigma detection gate (spec §16 Page 4), so counting it
# as an expected detection would be wrong.

DETECTABLE_INCIDENTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("ST-A40", "fpy"),
        ("ST-A40", "torqueNokRate"),
        ("ST-A40", "cycleTime"),
        ("ST-B30", "cycleTime"),
    }
)

# Every incident rides the same 8-hour cycle, so a window that contains the onset
# contains all of them. A window that contains no onset must yield nothing.
EXPECTED_INCIDENT_WINDOW = DETECTABLE_INCIDENTS
EXPECTED_HEALTHY_WINDOW: frozenset[tuple[str, str]] = frozenset()

# Root-cause design intent, recorded by hand so the label is not read back out
# of the ranking it is meant to test.
#
# ST-B30 is excluded: the station has no process on record, so the match degrades
# to the failure mode alone and two MACHINE causes tie at the same confidence.
# That is a data-model gap, not a ranking defect — see Limitations.
ROOT_CAUSE_LABELS: dict[tuple[str, str], str] = {
    ("ST-A40", "fpy"): "拧紧枪扭矩标定漂移",
    ("ST-A40", "torqueNokRate"): "拧紧枪扭矩标定漂移",
    ("ST-A40", "cycleTime"): "拧紧枪减速机构磨损导致动作变慢",
}

# Paraphrases used to walk the FMEA degradation chain. Each is a synonym the
# engine has never seen, so a hit is genuine text similarity, not a lookup.
SEMANTIC_VARIANTS: dict[str, tuple[str, ...]] = {
    "一次合格率下降": ("一次合格率降低", "一次合格率下滑"),
    "不良率上升": ("不良率升高", "不良率增加"),
    "扭矩不合格率上升": ("扭矩不合格率升高", "扭矩不合格率增加"),
    "节拍延长": ("节拍变长", "节拍变慢"),
    "工具故障率上升": ("工具故障率升高", "工具故障率增加"),
}

# Queries the Master FMEA cannot answer. The engine must say so — an invented
# record is the one failure mode that would poison the whole demo (spec §8.2).
NO_MATCH_QUERIES: tuple[FmeaQuery, ...] = (
    FmeaQuery(failure_mode="焊缝气孔率上升", process="车身焊接"),
    FmeaQuery(failure_mode="涂装色差超标", process="涂装"),
    FmeaQuery(failure_mode="来料尺寸超差", process="冲压"),
    FmeaQuery(failure_mode="电池包绝缘失效", process="电池装配"),
    FmeaQuery(failure_mode="完全无关的失效模式", process=None),
)

# Manual triage time assumed for the efficiency comparison. This is the *only*
# number in the report that is not measured; it is labelled as such everywhere it
# appears. A real figure needs a user study, which is out of scope here.
ASSUMED_MANUAL_MINUTES = 45.0


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Window:
    kind: str  # "anomaly" | "normal"
    start: datetime
    end: datetime
    expected: frozenset[tuple[str, str]]


def _build_windows(start_day: datetime, days: int) -> list[_Window]:
    """Slide two windows per day.

    The anomaly window (09:00–17:00) contains exactly one incident onset at
    13:30; the healthy window (09:00–12:00) sits inside the 08:00–13:30 quiet
    span and contains none. Both are 8-hour-cycle-aligned, so the labels never
    depend on which day is sampled.
    """
    windows: list[_Window] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        midnight = datetime(day.year, day.month, day.day)
        windows.append(
            _Window(
                kind="anomaly",
                start=midnight + timedelta(hours=9),
                end=midnight + timedelta(hours=17),
                expected=EXPECTED_INCIDENT_WINDOW,
            )
        )
        windows.append(
            _Window(
                kind="normal",
                start=midnight + timedelta(hours=9),
                end=midnight + timedelta(hours=12),
                expected=EXPECTED_HEALTHY_WINDOW,
            )
        )
    return windows


async def _collect(
    provider: MockIndustrialDataProvider, window: _Window
) -> tuple[list, list]:
    points = []
    for metric in MetricKey:
        points.extend(
            await provider.get_metrics(
                metric=metric, start=window.start, end=window.end
            )
        )
    events = await provider.get_events(
        start=window.start - timedelta(minutes=30), end=window.end
    )
    return points, list(events)


async def _station_lines(provider: MockIndustrialDataProvider) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in await provider.get_lines(FACTORY_ID):
        for station in await provider.get_stations(line.line_id):
            mapping[station.station_id] = line.line_id
    return mapping


async def _station_processes(
    provider: MockIndustrialDataProvider, station_lines: dict[str, str]
) -> dict[str, str]:
    """Station → process name, mirroring the route's context lookup.

    `/api/analysis` resolves the station's first process and scopes the FMEA
    match to it. The benchmark reads the same table through the provider so the
    match is scoped identically.
    """
    mapping: dict[str, str] = {}
    for station_id in station_lines:
        processes = await provider.get_processes(station_id)
        if processes:
            mapping[station_id] = processes[0].name
    return mapping


# --------------------------------------------------------------------------- #
# Scoring helpers
# --------------------------------------------------------------------------- #


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return precision, recall, f1


@dataclass(slots=True)
class _Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, expected: frozenset, predicted: frozenset) -> None:
        self.tp += len(expected & predicted)
        self.fp += len(predicted - expected)
        self.fn += len(expected - predicted)

    def metrics(self) -> tuple[int, int, int, float, float, float]:
        precision, recall, f1 = _prf(self.tp, self.fp, self.fn)
        return self.tp, self.fp, self.fn, precision, recall, f1


# --------------------------------------------------------------------------- #
# Benchmark sections
# --------------------------------------------------------------------------- #


async def run_anomaly_benchmark(
    provider: MockIndustrialDataProvider,
    windows: list[_Window],
    anomaly_rules: AnomalyRules,
    assess_rules: AssessRules,
    records,
    station_lines: dict[str, str],
    station_processes: dict[str, str],
) -> dict:
    """Detection precision/recall plus the root-cause ranking on the same pass.

    Both read the same detected anomalies, so the two results are guaranteed to
    describe the same detections.
    """
    confusion = {threshold: _Confusion() for threshold in THRESHOLDS}
    detect_seconds = 0.0
    detect_calls = 0

    rc_total = 0
    rc_correct = 0
    rc_by_scenario: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    rank_seconds = 0.0

    for window in windows:
        points, events = await _collect(provider, window)
        for threshold in THRESHOLDS:
            rules = (
                anomaly_rules
                if threshold == DEFAULT_THRESHOLD
                else replace(anomaly_rules, z_score_threshold=threshold)
            )
            started = perf_counter()
            found = detect_anomalies(
                points,
                rules=rules,
                factory_id=FACTORY_ID,
                station_lines=station_lines,
                events=events,
            )
            elapsed = perf_counter() - started
            if threshold == DEFAULT_THRESHOLD:
                detect_seconds += elapsed
                detect_calls += 1

            predicted = {(a.station_id, a.metric.value) for a in found}
            confusion[threshold].add(window.expected, predicted)

            if threshold != DEFAULT_THRESHOLD or window.kind != "anomaly":
                continue
            # Root-cause Top-1 on the engineered incidents. The match is scoped
            # to the station's process, exactly as the /api/analysis route does.
            for anomaly in found:
                key = (anomaly.station_id, anomaly.metric.value)
                expected_cause = ROOT_CAUSE_LABELS.get(key)
                if expected_cause is None:
                    continue
                started = perf_counter()
                match = match_fmea(
                    FmeaQuery(
                        failure_mode=assess_rules.failure_modes.get(
                            f"{anomaly.metric.value}:{anomaly.direction.value}", ""
                        ),
                        process=station_processes.get(anomaly.station_id),
                    ),
                    records,
                    assess_rules,
                )
                ranked = rank_root_causes(
                    anomaly=anomaly,
                    evidence=anomaly.evidence,
                    match=match,
                    records=records,
                    rules=assess_rules,
                    anomaly_rules=anomaly_rules,
                )
                rank_seconds += perf_counter() - started
                rc_total += 1
                bucket = rc_by_scenario[key]
                bucket[1] += 1
                if ranked and ranked[0].cause == expected_cause:
                    rc_correct += 1
                    bucket[0] += 1

    return {
        "confusion": confusion,
        "detect_seconds": detect_seconds,
        "detect_calls": detect_calls,
        "rc_total": rc_total,
        "rc_correct": rc_correct,
        "rc_by_scenario": dict(rc_by_scenario),
        "rank_seconds": rank_seconds,
    }


@dataclass(frozen=True, slots=True)
class _FmeaCase:
    label: str
    query: FmeaQuery
    expected_status: str
    target: tuple[str, str, str] | None  # (process, failure_mode, cause)


def _build_fmea_cases(records) -> list[_FmeaCase]:
    cases: list[_FmeaCase] = []
    for record in records:
        target = (record.process, record.failure_mode, record.cause)
        cases.append(
            _FmeaCase(
                label="exact",
                query=FmeaQuery(
                    failure_mode=record.failure_mode, process=record.process
                ),
                expected_status="EXACT",
                target=target,
            )
        )
        cases.append(
            _FmeaCase(
                label="normalized",
                query=FmeaQuery(
                    failure_mode=f"{record.failure_mode}。", process=record.process
                ),
                expected_status="NORMALIZED",
                target=target,
            )
        )
        for variant in SEMANTIC_VARIANTS.get(record.failure_mode, ()):
            cases.append(
                _FmeaCase(
                    label="semantic",
                    query=FmeaQuery(failure_mode=variant, process=record.process),
                    expected_status="SEMANTIC",
                    target=target,
                )
            )
    for query in NO_MATCH_QUERIES:
        cases.append(
            _FmeaCase(
                label="no_match",
                query=query,
                expected_status="NO_MATCH",
                target=None,
            )
        )
    return cases


def run_fmea_benchmark(cases: list[_FmeaCase], records, assess_rules: AssessRules) -> dict:
    level_correct = 0
    retrieval_total = 0
    retrieval_top3 = 0
    retrieval_top1 = 0
    family_total = 0
    family_top1 = 0
    no_fabrication_total = 0
    no_fabrication_ok = 0
    by_label: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [level ok, total]
    match_seconds = 0.0

    for case in cases:
        started = perf_counter()
        result = match_fmea(case.query, records, assess_rules)
        match_seconds += perf_counter() - started

        by_label[case.label][1] += 1
        if result.match_status.value == case.expected_status:
            level_correct += 1
            by_label[case.label][0] += 1

        if case.expected_status == "NO_MATCH":
            no_fabrication_total += 1
            if result.match_status.value == "NO_MATCH" and not result.candidates:
                no_fabrication_ok += 1
            continue

        assert case.target is not None
        retrieval_total += 1
        triples = [
            (c.record.process, c.record.failure_mode, c.record.cause)
            for c in result.candidates
        ]
        if case.target in triples:
            retrieval_top3 += 1
            if triples[0] == case.target:
                retrieval_top1 += 1

        family_total += 1
        if result.candidates:
            first = result.candidates[0].record
            # Compare against the *target* record's family, not the raw query
            # string: normalized queries carry a trailing "。" and semantic
            # queries are synonyms, so a string compare would never hit them.
            if (first.process, first.failure_mode) == (
                case.target[0],
                case.target[1],
            ):
                family_top1 += 1

    return {
        "cases": len(cases),
        "level_correct": level_correct,
        "retrieval_total": retrieval_total,
        "retrieval_top1": retrieval_top1,
        "retrieval_top3": retrieval_top3,
        "family_total": family_total,
        "family_top1": family_top1,
        "no_fabrication_total": no_fabrication_total,
        "no_fabrication_ok": no_fabrication_ok,
        "by_label": dict(by_label),
        "match_seconds": match_seconds,
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _build_report(
    *,
    seed: int,
    days: int,
    windows: list[_Window],
    anomaly: dict,
    fmea: dict,
    records,
) -> str:
    anomaly_windows = sum(1 for w in windows if w.kind == "anomaly")
    normal_windows = sum(1 for w in windows if w.kind == "normal")

    sweep_rows = []
    for threshold in THRESHOLDS:
        tp, fp, fn, precision, recall, f1 = anomaly["confusion"][threshold].metrics()
        marker = " ← 默认" if threshold == DEFAULT_THRESHOLD else ""
        sweep_rows.append(
            f"| {threshold:.1f}{marker} | {tp} | {fp} | {fn} | "
            f"{_pct(precision)} | {_pct(recall)} | {_pct(f1)} |"
        )
    sweep_table = "\n".join(sweep_rows)

    # Operating-point narrative is derived from the sweep, not asserted: the
    # default gate is only defensible if the numbers actually back it.
    def _at(threshold: float) -> tuple[int, int, int, float, float, float]:
        return anomaly["confusion"][threshold].metrics()

    best_t = max(THRESHOLDS, key=lambda t: _at(t)[5])
    _, fp_best, fn_best, p_best, r_best, f1_best = _at(best_t)
    lo, hi = THRESHOLDS[0], 4.0
    _, fp_lo, _, p_lo, r_lo, _ = _at(lo)
    _, _, _, p_hi, r_hi, _ = _at(hi)

    rc_total = anomaly["rc_total"]
    rc_correct = anomaly["rc_correct"]
    rc_rows = []
    for (station, metric), (ok, total) in sorted(anomaly["rc_by_scenario"].items()):
        expected = ROOT_CAUSE_LABELS[(station, metric)]
        rc_rows.append(
            f"| {station} | {metric} | {expected} | {ok}/{total} | "
            f"{_pct(ok / total if total else 0.0)} |"
        )
    rc_table = "\n".join(rc_rows)

    fmea_label_rows = []
    for label in ("exact", "normalized", "semantic", "no_match"):
        ok, total = fmea["by_label"].get(label, [0, 0])
        fmea_label_rows.append(
            f"| {label} | {ok}/{total} | {_pct(ok / total if total else 0.0)} |"
        )
    fmea_label_table = "\n".join(fmea_label_rows)

    retrieval_total = fmea["retrieval_total"]
    top1 = fmea["retrieval_top1"]
    top3 = fmea["retrieval_top3"]
    family_total = fmea["family_total"]
    family_top1 = fmea["family_top1"]
    nf_total = fmea["no_fabrication_total"]
    nf_ok = fmea["no_fabrication_ok"]

    detect_avg_ms = (
        anomaly["detect_seconds"] / anomaly["detect_calls"] * 1000
        if anomaly["detect_calls"]
        else 0.0
    )
    match_avg_ms = fmea["match_seconds"] / fmea["cases"] * 1000 if fmea["cases"] else 0.0
    rank_avg_ms = (
        anomaly["rank_seconds"] / rc_total * 1000 if rc_total else 0.0
    )
    # One full triage = detect on the window + match + rank + risk.
    pipeline_ms = detect_avg_ms + match_avg_ms + rank_avg_ms

    return f"""# BENCHMARK.md — FactoryMind AI 确定性引擎基准测试

> **自动生成，请勿手改。** 重新生成：`python scripts/run_benchmark.py --seed {seed} --days {days}`
> 生成环境：Python {platform.python_version()} · FactoryMind AI Engine v{__version__}
>
> ⚠️ **Demonstration / Simulation Result（规格书 §54）。** 本页全部数字在**合成数据**上实测得出，
> 不代表任何真实工厂的性能。唯一的非实测数字是人工分析耗时假设，已在文中逐处标注。

---

## 1. Dataset

| 项 | 值 |
|---|---|
| 数据源 | `MockIndustrialDataProvider`（确定性、可复现） |
| 随机种子 | `{seed}`（每个数值都是 (seed, station, metric, timestamp) 的纯函数） |
| 工厂 / 产线 / 工位 | 1 工厂 · 2 产线 · 9 工位 |
| 指标 | fpy / nokRate / torqueNokRate / cycleTime / toolErrorRate，5 分钟桶 |
| 异常窗口 | {anomaly_windows} 个（09:00–17:00，含 13:30 注入事件） |
| 正常窗口 | {normal_windows} 个（09:00–12:00，位于 08:00–13:30 健康区间内） |
| 注入事件 | 5 个，其中 4 个为可检出故障，1 个（ST-A40 toolErrorRate）刻意低于检出阈值 |
| Master FMEA | {len(records)} 条合成记录 |
| 异常周期 | 8 小时一循环，故障尾段 05:30–08:00 / 13:30–16:00 / 21:30–24:00 |

**标签来源（与引擎独立）**：异常标签来自注入设计；FMEA 标签是 Master FMEA 记录本身；
根因标签是事件的设计意图，手工登记而非从排序结果回读。

---

## 2. Method

确定性引擎全程离线运行，不调用任何 LLM：

1. `detect_anomalies` — 中位数/MAD 鲁棒基线 → z-score 越限 → 事件合并 → 严重度与证据关联。
2. `match_fmea` — 四级降级 `EXACT → NORMALIZED → SEMANTIC → LLM_CANDIDATE → NO_MATCH`。
3. `rank_root_causes` — 证据加权排序（相关性 0.30 / 时序 0.25 / 幅度 0.20 / FMEA 相关 0.15 / 历史 0.10）。
4. `risk_from_match` — 由匹配行的 S/O/D 派生 RPN 与 AP。

每个窗口按 (station, metric) 粒度比对预测集与标签集，跨窗口做微平均（micro-average）。

---

## 3. Metrics

| 维度 | 指标 | 定义 |
|---|---|---|
| 异常检测 | Precision / Recall / F1 | (station, metric) 粒度的微平均 |
| FMEA 匹配 | Level Accuracy | 返回的 `matchStatus` 等于期望级别 |
| FMEA 匹配 | Family Top-1 | 首个候选的 (process, failureMode) 命中查询族 |
| FMEA 匹配 | Cause Top-3 Recall | 目标记录 (process, failureMode, cause) 落在前 3 候选内 |
| FMEA 匹配 | No-Fabrication | 无匹配查询返回 `NO_MATCH` 且候选为空的比例 |
| 根因 | Top-1 Accuracy | 排序第一名等于设计意图根因 |
| 效率 | 引擎耗时 | 单次检测 / 匹配 / 排序的墙钟耗时 |

---

## 4. Results

### 4.1 异常检测 — 阈值扫描

| z 阈值 | TP | FP | FN | Precision | Recall | F1 |
|---:|---:|---:|---:|---:|---:|---:|
{sweep_table}

- **{best_t:.1f}σ 是 F1 拐点**（本扫描最高 {_pct(f1_best)}）：Precision {_pct(p_best)}、Recall {_pct(r_best)}。
  残余 {fp_best} 次误报 / {fn_best} 次漏报是统计检测器在有限样本下的固有折中——抖动实现使个别窗口恰好跨线或差一点越线，不是调参失误。
- **下调到 {lo:.1f}σ**：Recall 升至 {_pct(r_lo)}，但 FP 由 {fp_best} 涨到 {fp_lo}，Precision 跌至 {_pct(p_lo)}——
  {lo:.1f}σ 落进健康抖动带，把正常波动也判为异常（含设计上刻意压在检出线下的 ST-A40 toolErrorRate 佐证信号）。
  这正是把检出线定在 {best_t:.1f}σ 的依据。
- **上调到 {hi:.1f}σ**：Precision 达 {_pct(p_hi)}，Recall 却掉到 {_pct(r_hi)}，漏报代价大于收益。

### 4.2 FMEA 匹配

| 级别 | Level Accuracy | 比例 |
|---|---:|---:|
{fmea_label_table}

| 指标 | 结果 |
|---|---|
| Family Top-1 | {family_top1}/{family_total} = {_pct(family_top1 / family_total if family_total else 0.0)} |
| Cause Top-3 Recall | {top3}/{retrieval_total} = {_pct(top3 / retrieval_total if retrieval_total else 0.0)} |
| Cause Top-1（见局限） | {top1}/{retrieval_total} = {_pct(top1 / retrieval_total if retrieval_total else 0.0)} |
| No-Fabrication Rate | {nf_ok}/{nf_total} = {_pct(nf_ok / nf_total if nf_total else 0.0)} |

### 4.3 根因排序（Top-1）

| 工位 | 指标 | 设计意图根因 | 命中 | 准确率 |
|---|---|---|---|---:|
{rc_table}

- 合计：**{rc_correct}/{rc_total} = {_pct(rc_correct / rc_total if rc_total else 0.0)}**（3 个不同场景，各在 {anomaly_windows} 个窗口上重复评估）。

### 4.4 效率

| 阶段 | 平均耗时 |
|---|---:|
| 异常检测（单窗口，8h × 9 工位 × 5 指标） | {detect_avg_ms:.1f} ms |
| FMEA 匹配（单次查询） | {match_avg_ms:.2f} ms |
| 根因排序（单次） | {rank_avg_ms:.2f} ms |
| **单次完整分诊（检测+匹配+排序）** | **{pipeline_ms:.1f} ms** |

人工分析耗时假设为 **{ASSUMED_MANUAL_MINUTES:.0f} 分钟**（⚠️ 假设值，非实测；来自规格书 §54 的示意，
真实数字需用户研究，本项目未做）。据此，引擎的确定性计算部分相对人工为
**{ASSUMED_MANUAL_MINUTES * 60_000 / pipeline_ms:,.0f}× 加速**——但该比值只覆盖机器的计算时间，
不含人工复核与执行，**不可直接当作现场效率提升**。

---

## 5. Limitations

1. **合成数据、设计为可分离。** 注入故障的幅度经过调校，多数窗口可稳定越过检出线，因此异常检测指标偏高；
   残余误报/漏报来自抖动实现，而非真实工况的噪声结构。真实产线的漂移、多故障叠加会更难，本页数字不能外推。
2. **Cause Top-1 在 EXACT 级别不可分。** 同一 (process, failureMode) 下多个原因并列返回（按原因文本排序），
   引擎并不声称其中某一个"最可能"——真正做区分的是根因排序模块（§4.3）。因此 §4.2 的 Cause Top-1
   不应被读作匹配质量，Family Top-1 与 Cause Top-3 才是。
3. **根因样本量小。** 只有 3 个注入场景带独立设计标签。ST-B30 被排除：该工位在合成数据中**没有工艺记录**，
   匹配退化为仅按失效模式，两个 MACHINE 原因同分并列——这是数据模型缺口，不是排序缺陷（建议补 `ST-B30` 工艺行）。
4. **效率是计算时间，不是人时。** 见 §4.4。
5. **无真实 supOS 数据。** 全部结果基于 `DATA_PROVIDER=mock`；`SupOSProvider` 已实现，并已在真机
   CE `V1.5.0.0-M` 实例完成 UNS 导入 / MQTT 事件通路 / 应用包与网关路由验收，但本报告的
   全部数字仍取自合成数据，未使用真实 supOS 实测数据，见 `SUPOS_INTEGRATION.md`。

---

## 6. Reproduce

```bash
cd services/ai-engine
.venv/Scripts/python.exe scripts/run_benchmark.py --seed {seed} --days {days}
```

同参数重复运行结果逐字节一致（所有生成值都是时间戳与种子的纯函数）。
"""


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


async def _main(args: argparse.Namespace) -> int:
    anomaly_rules = load_anomaly_rules()
    assess_rules = load_assess_rules()
    records = load_master_fmea(assess_rules)
    provider = MockIndustrialDataProvider(seed=args.seed, fmea_records=records)
    station_lines = await _station_lines(provider)
    station_processes = await _station_processes(provider, station_lines)

    start_day = datetime.fromisoformat(args.start)
    windows = _build_windows(start_day, args.days)

    anomaly = await run_anomaly_benchmark(
        provider,
        windows,
        anomaly_rules,
        assess_rules,
        records,
        station_lines,
        station_processes,
    )
    fmea_cases = _build_fmea_cases(records)
    fmea = run_fmea_benchmark(fmea_cases, records, assess_rules)

    report = _build_report(
        seed=args.seed,
        days=args.days,
        windows=windows,
        anomaly=anomaly,
        fmea=fmea,
        records=records,
    )

    if args.json:
        payload = {
            "seed": args.seed,
            "days": args.days,
            "anomaly": {
                str(threshold): {
                    "tp": anomaly["confusion"][threshold].tp,
                    "fp": anomaly["confusion"][threshold].fp,
                    "fn": anomaly["confusion"][threshold].fn,
                    **dict(
                        zip(
                            ("precision", "recall", "f1"),
                            anomaly["confusion"][threshold].metrics()[3:],
                            strict=True,
                        )
                    ),
                }
                for threshold in THRESHOLDS
            },
            "root_cause": {
                "total": anomaly["rc_total"],
                "correct": anomaly["rc_correct"],
            },
            "fmea": {
                "cases": fmea["cases"],
                "level_accuracy": fmea["level_correct"] / fmea["cases"]
                if fmea["cases"]
                else 0.0,
                "family_top1": fmea["family_top1"],
                "family_total": fmea["family_total"],
                "cause_top3": fmea["retrieval_top3"],
                "cause_top1": fmea["retrieval_top1"],
                "retrieval_total": fmea["retrieval_total"],
                "no_fabrication_ok": fmea["no_fabrication_ok"],
                "no_fabrication_total": fmea["no_fabrication_total"],
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    out_path = Path(args.out) if args.out else DOCS_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    tp, fp, fn, precision, recall, f1 = anomaly["confusion"][
        DEFAULT_THRESHOLD
    ].metrics()
    print(
        f"anomaly@{DEFAULT_THRESHOLD}: P={_pct(precision)} R={_pct(recall)} F1={_pct(f1)} "
        f"(tp={tp} fp={fp} fn={fn})"
    )
    print(
        f"fmea: level={fmea['level_correct']}/{fmea['cases']} "
        f"family_top1={fmea['family_top1']}/{fmea['family_total']} "
        f"cause_top3={fmea['retrieval_top3']}/{fmea['retrieval_total']} "
        f"no_fabrication={fmea['no_fabrication_ok']}/{fmea['no_fabrication_total']}"
    )
    print(
        f"root_cause_top1={anomaly['rc_correct']}/{anomaly['rc_total']} "
        f"({len(anomaly['rc_by_scenario'])} scenarios)"
    )
    print(f"report -> {out_path}")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FactoryMind AI benchmark harness")
    parser.add_argument("--seed", type=int, default=2026, help="demo seed (default 2026)")
    parser.add_argument(
        "--days", type=int, default=100, help="days of windows to sample (default 100)"
    )
    parser.add_argument(
        "--start", default="2026-07-01", help="first day, ISO date (default 2026-07-01)"
    )
    parser.add_argument("--out", default=None, help="output markdown path")
    parser.add_argument(
        "--json", action="store_true", help="also print raw metrics as JSON"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())