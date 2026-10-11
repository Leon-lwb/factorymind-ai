"""Analysis surface (spec §8, §14, §15, §25).

The route gathers FMEA reference data and process context, then runs the
deterministic assess pipeline — match, rank, risk — before handing the computed
facts to the AI provider for explanation. It holds no analytics of its own, so
the same pipeline is reachable from tests and the benchmark harness.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import AnomalyRules, AssessRules
from app.domain.models import (
    AnalysisRequest,
    AnalysisResponse,
    Equipment,
    FmeaMatchRequest,
    FmeaMatchResult,
    FmeaRecord,
    Process,
)
from app.engine.fmea import FmeaQuery, match_fmea
from app.engine.risk import risk_from_match
from app.engine.rootcause import rank_root_causes
from app.providers.ai import AiAnalysisRequest, AiProvider
from app.providers.base import IndustrialDataProvider

router = APIRouter(prefix="/api")


def _provider(request: Request) -> IndustrialDataProvider:
    return request.app.state.data_provider


def _assess(request: Request) -> AssessRules:
    return request.app.state.assess_rules


def _anomaly_rules(request: Request) -> AnomalyRules:
    return request.app.state.anomaly_rules


def _ai(request: Request) -> AiProvider:
    return request.app.state.ai_provider


async def _context(
    provider: IndustrialDataProvider, station_id: str, process_id: str | None
) -> tuple[Process | None, Equipment | None]:
    """Process and equipment of the station — context for the explanation."""
    processes = list(await provider.get_processes(station_id))
    process = next((item for item in processes if item.process_id == process_id), None)
    if process is None and processes:
        process = processes[0]
    equipment = next(iter(await provider.get_equipment(station_id)), None)
    return process, equipment


@router.post("/analysis", response_model=AnalysisResponse)
async def analysis(request: Request, body: AnalysisRequest) -> AnalysisResponse:
    assess = _assess(request)
    anomaly_rules = _anomaly_rules(request)
    provider = _provider(request)
    anomaly = body.anomaly

    records = await provider.get_fmea_records()
    process, equipment = await _context(provider, anomaly.station_id, body.process_id)
    failure_mode = assess.failure_modes.get(
        f"{anomaly.metric.value}:{anomaly.direction.value}", ""
    )
    # FMEA is process-scoped: the same failure mode means different causes at a
    # fastening station than at a re-check station, so the station's process is a
    # match key whenever one is known. With no process on record the lookup
    # degrades to the failure mode alone rather than to no lookup at all.
    match = match_fmea(
        FmeaQuery(failure_mode=failure_mode, process=process.name if process else None),
        records,
        assess,
    )

    root_causes = rank_root_causes(
        anomaly=anomaly,
        evidence=anomaly.evidence,
        match=match,
        records=records,
        rules=assess,
        anomaly_rules=anomaly_rules,
    )
    risk = risk_from_match(match, anomaly, assess)
    metric_rule = anomaly_rules.metrics.get(anomaly.metric.value)

    result = await _ai(request).analyze(
        AiAnalysisRequest(
            anomaly=anomaly,
            evidence=anomaly.evidence,
            process=process,
            equipment=equipment,
            root_causes=root_causes,
            risk=risk,
            fmea_match=match,
            metric_label=metric_rule.label if metric_rule else anomaly.metric.value,
        )
    )
    return AnalysisResponse(anomaly=anomaly, fmea_match=match, analysis=result)


@router.get("/fmea/records", response_model=list[FmeaRecord])
async def fmea_records(request: Request) -> list[FmeaRecord]:
    """The whole Master FMEA, RPN and AP already derived from S/O/D at load.

    Reference data only — the risk matrix and the record table both read it, and
    neither recomputes a score the engine already owns (spec §6.1).
    """
    return list(await _provider(request).get_fmea_records())


@router.post("/fmea/match", response_model=FmeaMatchResult)
async def fmea_match(request: Request, body: FmeaMatchRequest) -> FmeaMatchResult:
    records = await _provider(request).get_fmea_records()
    query = FmeaQuery(
        failure_mode=body.failure_mode,
        process=body.process,
        operation=body.operation,
    )
    return match_fmea(query, records, _assess(request))