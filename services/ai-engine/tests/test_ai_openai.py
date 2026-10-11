"""Tests for the OpenAI-compatible explanation layer (spec §6.1, §7, §20, §23).

No network: every case drives `OpenAiCompatibleAiProvider` through
`httpx.MockTransport`, so the wire parsing, the retry policy and the three code-
enforced rules are all verified offline.

The rules under test are the ones the project cannot afford to get wrong:

* the model supplies no number the engine owns — risk and action priority come
  from the request, whatever the model returns;
* a recommendation citing evidence that does not exist rejects the whole
  response;
* a malformed response is an error, never a silent fall back to the mock.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime

import httpx
import pytest
from app.domain.models import (
    ActionPriority,
    Anomaly,
    AnomalyDirection,
    AnomalyStatus,
    Equipment,
    EquipmentType,
    Evidence,
    EvidenceType,
    FmeaCandidate,
    FmeaMatchResult,
    FmeaMatchStatus,
    FmeaRecord,
    FourMOneECategory,
    MetricKey,
    Process,
    RiskAssessment,
    RiskLevel,
    RootCauseCandidate,
)
from app.providers.ai import AiAnalysisRequest, AiProviderError
from app.providers.ai_openai import OpenAiCompatibleAiProvider

ANOMALY_AT = datetime(2026, 10, 1, 10, 0, 0)
GENERATED_AT = datetime(2026, 10, 1, 10, 5, 0)

_EVIDENCE: tuple[Evidence, ...] = (
    Evidence(
        id="EV-1",
        type=EvidenceType.METRIC_SHIFT,
        source="fpy@ST-A40",
        value=91.0,
        relevance=0.9,
        timestamp=ANOMALY_AT,
    ),
    Evidence(
        id="EV-2",
        type=EvidenceType.EVENT_CORRELATION,
        source="TOOL_ERROR#778",
        value=1.0,
        relevance=0.7,
        timestamp=ANOMALY_AT,
    ),
)


def _anomaly() -> Anomaly:
    return Anomaly(
        id="AN-1",
        timestamp=ANOMALY_AT,
        factory_id="FAC-01",
        line_id="LINE-A",
        station_id="ST-A40",
        metric=MetricKey.FPY,
        baseline=98.0,
        actual=91.0,
        delta=-7.0,
        delta_ratio=-0.0714,
        direction=AnomalyDirection.DOWN,
        z_score=-3.2,
        severity=0.82,
        status=AnomalyStatus.NEW,
        window_start=datetime(2026, 10, 1, 9, 0, 0),
        window_end=datetime(2026, 10, 1, 10, 0, 0),
        evidence=list(_EVIDENCE),
    )


def _fmea_match() -> FmeaMatchResult:
    record = FmeaRecord(
        process="关键扭矩拧紧",
        operation="拧紧",
        failure_mode="扭矩不足",
        effect="连接失效",
        cause="拧紧枪标定漂移",
        prevention_control="定期标定",
        detection_control="扭矩监控",
        severity=8,
        occurrence=4,
        detection=3,
        rpn=96,
        ap=ActionPriority.HIGH,
        recommended_action="重新标定拧紧枪并复检首件",
        category=FourMOneECategory.MACHINE,
    )
    return FmeaMatchResult(
        match_status=FmeaMatchStatus.NORMALIZED,
        requires_human_review=False,
        candidates=[
            FmeaCandidate(record=record, score=0.86, match_type=FmeaMatchStatus.NORMALIZED)
        ],
    )


def _request(*, ap: ActionPriority = ActionPriority.HIGH) -> AiAnalysisRequest:
    return AiAnalysisRequest(
        anomaly=_anomaly(),
        evidence=list(_EVIDENCE),
        process=Process(
            process_id="PR-A40-01",
            station_id="ST-A40",
            name="关键扭矩拧紧",
            operation="拧紧",
        ),
        equipment=Equipment(
            equipment_id="EQ-A40-01",
            station_id="ST-A40",
            type=EquipmentType.TORQUE_TOOL,
            name="电动拧紧枪 #1",
        ),
        root_causes=[
            RootCauseCandidate(
                cause="拧紧枪标定漂移",
                confidence=0.72,
                evidence_ids=["EV-1", "EV-2"],
                category=FourMOneECategory.MACHINE,
            )
        ],
        risk=RiskAssessment(severity=8, occurrence=4, detection=3, rpn=96, ap=ap),
        fmea_match=_fmea_match(),
        metric_label="首次通过率 FPY",
    )


def _completion(content: str, *, status: int = 200) -> httpx.Response:
    """An OpenAI chat-completions envelope wrapping `content`."""
    return httpx.Response(
        status,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        },
    )


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str | None = "test-key",
    max_retries: int = 2,
) -> OpenAiCompatibleAiProvider:
    return OpenAiCompatibleAiProvider(
        base_url="https://ai.example.test/v1",
        model="test-model",
        api_key=api_key,
        max_retries=max_retries,
        clock=lambda: GENERATED_AT,
        transport=httpx.MockTransport(handler),
        backoff=lambda _attempt: 0.0,
    )


_GOOD = json.dumps(
    {
        "summary": "FPY 在 ST-A40 下降：基线 98 → 实测 91（z=-3.2σ，幅度 7.1%）。",
        "recommended_actions": [
            {
                "action": "重新标定拧紧枪并复检首件",
                "rationale": "来自 FMEA「拧紧枪标定漂移」的既定措施",
                "evidence_ids": ["EV-1", "EV-2", "EV-1"],
            }
        ],
    },
    ensure_ascii=False,
)


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_analyze_builds_the_result_from_a_valid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["temperature"] == 0
        assert body["stream"] is False
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        return _completion(_GOOD)

    provider = _provider(handler)
    result = asyncio.run(provider.analyze(_request()))

    assert result.summary.startswith("FPY 在 ST-A40 下降")
    assert result.provider == "openai-compatible"
    assert result.generated_at == GENERATED_AT
    assert result.root_cause_candidates == _request().root_causes
    assert result.risk == _request().risk
    assert len(result.recommended_actions) == 1
    action = result.recommended_actions[0]
    assert action.action == "重新标定拧紧枪并复检首件"
    # Duplicate citations collapse, order preserved.
    assert action.evidence_ids == ["EV-1", "EV-2"]
    assert action.rationale == "来自 FMEA「拧紧枪标定漂移」的既定措施"
    # Urgency is derived from the deterministic AP, not the model.
    assert action.priority is RiskLevel.HIGH


def test_action_priority_follows_the_deterministic_ap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(_GOOD)

    provider = _provider(handler)
    result = asyncio.run(provider.analyze(_request(ap=ActionPriority.LOW)))
    assert result.recommended_actions[0].priority is RiskLevel.LOW


def test_model_supplied_numbers_are_ignored() -> None:
    """A model that tries to set its own risk or priority cannot override the engine."""
    content = json.dumps(
        {
            "summary": "结论。",
            "risk": {"severity": 1, "occurrence": 1, "detection": 1, "rpn": 1, "ap": "L"},
            "recommended_actions": [
                {"action": "措施", "priority": "LOW", "evidence_ids": []}
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(content)

    result = asyncio.run(_provider(handler).analyze(_request(ap=ActionPriority.HIGH)))
    assert result.risk == _request().risk
    assert result.recommended_actions[0].priority is RiskLevel.HIGH


def test_json_wrapped_in_a_markdown_fence_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(f"```json\n{_GOOD}\n```")

    result = asyncio.run(_provider(handler).analyze(_request()))
    assert result.recommended_actions[0].action == "重新标定拧紧枪并复检首件"


def test_json_surrounded_by_prose_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(f"好的，以下是结果：\n{_GOOD}\n以上。")

    result = asyncio.run(_provider(handler).analyze(_request()))
    assert result.summary.startswith("FPY 在 ST-A40 下降")


# --------------------------------------------------------------------------- #
# Evidence citation — the rule that cannot be softened
# --------------------------------------------------------------------------- #


def test_action_citing_unknown_evidence_is_rejected() -> None:
    content = json.dumps(
        {
            "summary": "结论。",
            "recommended_actions": [
                {"action": "措施", "evidence_ids": ["EV-1", "EV-999"]}
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(content)

    with pytest.raises(AiProviderError, match="EV-999"):
        asyncio.run(_provider(handler).analyze(_request()))


def test_action_without_evidence_is_allowed_when_the_list_is_empty() -> None:
    """An action may cite nothing; it may not cite something that is not there."""
    content = json.dumps(
        {"summary": "结论。", "recommended_actions": [{"action": "措施", "evidence_ids": []}]}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(content)

    result = asyncio.run(_provider(handler).analyze(_request()))
    assert result.recommended_actions[0].evidence_ids == []


# --------------------------------------------------------------------------- #
# Malformed responses are errors, not degraded answers
# --------------------------------------------------------------------------- #


def test_non_json_content_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _completion("抱歉，我无法回答。")

    with pytest.raises(AiProviderError, match="not a JSON object"):
        asyncio.run(_provider(handler).analyze(_request()))


def test_missing_summary_raises() -> None:
    content = json.dumps({"recommended_actions": [{"action": "措施"}]})

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(content)

    with pytest.raises(AiProviderError, match="summary"):
        asyncio.run(_provider(handler).analyze(_request()))


def test_empty_action_list_raises() -> None:
    content = json.dumps({"summary": "结论。", "recommended_actions": []})

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(content)

    with pytest.raises(AiProviderError, match="at least one recommended action"):
        asyncio.run(_provider(handler).analyze(_request()))


def test_empty_action_text_raises() -> None:
    content = json.dumps(
        {"summary": "结论。", "recommended_actions": [{"action": "   "}]}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(content)

    with pytest.raises(AiProviderError, match=r"recommended_actions\[0\].action"):
        asyncio.run(_provider(handler).analyze(_request()))


def test_malformed_evidence_ids_type_raises() -> None:
    content = json.dumps(
        {"summary": "结论。", "recommended_actions": [{"action": "措施", "evidence_ids": "EV-1"}]}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(content)

    with pytest.raises(AiProviderError, match="evidence_ids"):
        asyncio.run(_provider(handler).analyze(_request()))


def test_missing_choices_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "chat.completion", "choices": []})

    with pytest.raises(AiProviderError, match="choices"):
        asyncio.run(_provider(handler).analyze(_request()))


def test_non_json_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    with pytest.raises(AiProviderError, match="non-JSON body"):
        asyncio.run(_provider(handler).analyze(_request()))


# --------------------------------------------------------------------------- #
# Transport: status handling, retries, unreachable endpoint
# --------------------------------------------------------------------------- #


def test_client_error_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(AiProviderError, match="HTTP 401"):
        asyncio.run(_provider(handler).analyze(_request()))
    assert calls["n"] == 1


def test_transient_error_is_retried_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="gateway down")
        return _completion(_GOOD)

    result = asyncio.run(_provider(handler).analyze(_request()))
    assert result.recommended_actions[0].action == "重新标定拧紧枪并复检首件"
    assert calls["n"] == 2


def test_transient_error_exhausts_retries_and_raises() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="gateway down")

    with pytest.raises(AiProviderError, match="HTTP 503"):
        asyncio.run(_provider(handler, max_retries=2).analyze(_request()))
    assert calls["n"] == 3


def test_unreachable_endpoint_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(AiProviderError, match="unreachable"):
        asyncio.run(_provider(handler, max_retries=0).analyze(_request()))


def test_no_api_key_omits_the_authorization_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return _completion(_GOOD)

    asyncio.run(_provider(handler, api_key=None).analyze(_request()))


# --------------------------------------------------------------------------- #
# Construction guards
# --------------------------------------------------------------------------- #


def test_blank_base_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="base URL"):
        OpenAiCompatibleAiProvider(base_url="   ", model="test-model")


def test_blank_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="model"):
        OpenAiCompatibleAiProvider(base_url="https://ai.example.test/v1", model="  ")