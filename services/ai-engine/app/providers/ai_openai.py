"""OpenAI-compatible explanation layer (spec §6.1, §7, §15, §20).

Any server speaking the OpenAI chat-completions API can drive this layer —
DeepSeek, Qwen/DashScope, Moonshot, a local Ollama, or OpenAI itself. The
transport is plain `httpx`, so no vendor SDK enters the dependency tree.

The engine still owns every number. This provider hands the model the
already-computed anomaly, ranked causes, FMEA match and risk, and asks only for
prose and recommendations. Three rules are enforced in code rather than trusted
to the prompt:

* **The model supplies no number the engine owns.** `risk`, the ranked causes,
  the action priority and the timestamp are injected from the request; whatever
  the model returns for those keys is ignored.
* **Every recommendation must cite evidence that exists.** An action citing an
  id absent from the anomaly's evidence rejects the whole response. A fabricated
  citation is precisely the failure this project exists to avoid.
* **A malformed response is an error, not a degraded answer.** Non-JSON, a
  missing field, a wrong type or an empty action list raises `AiProviderError`;
  there is no silent fall back to the mock (spec §23).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import httpx

from app.domain.models import AiAnalysisResult, RecommendedAction, RiskLevel
from app.providers.ai import AP_TO_RISK, AiAnalysisRequest, AiProviderError

# Transient conditions worth a retry: rate limits, gateway hiccups, timeouts.
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})

_SYSTEM_PROMPT = """\
你是汽车总装线的质量工程助手，只负责"解释"与"建议"，不做任何计算。

硬性规则：
1. 只能使用下方"事实"中出现的数值，不得引入任何新的数字、阈值或置信度。
2. 不得输出或修改根因排序与风险评分——它们已由确定性引擎给出，你只能引用。
3. 每条措施都必须给出 evidence_ids，且只能引用下方"证据"中存在的 id；
   引用不存在的 id 会导致整个回复被系统拒绝。
4. 至少给出一条措施。
5. 只输出一个 JSON 对象，不要输出 Markdown 代码块、前后说明或多余文字。
"""

_SCHEMA_HINT = """\
{
  "summary": "一段中文结论，引用上方数值，不得引入新数字",
  "recommended_actions": [
    {
      "action": "措施文本",
      "rationale": "为什么这么做，可引用根因或 FMEA 既定措施",
      "evidence_ids": ["证据 id"]
    }
  ]
}"""


def _default_backoff(attempt: int) -> float:
    return min(0.5 * (2**attempt), 4.0)


def _fact_sheet(request: AiAnalysisRequest) -> str:
    """Render the computed facts the model is allowed to see, and nothing else."""
    anomaly = request.anomaly
    lines: list[str] = ["## 事实（只可使用这里的数值）"]
    lines.append(
        f"- 指标：{request.metric_label}（{anomaly.metric.value}）@ 工位 {anomaly.station_id}"
    )
    lines.append(
        f"- 方向：{anomaly.direction.value}；基线 {anomaly.baseline:g}；实测 {anomaly.actual:g}"
    )
    lines.append(
        f"- 偏差 {anomaly.delta:g}（{anomaly.delta_ratio * 100:.1f}%）；"
        f"z={anomaly.z_score:g}σ；严重度 {anomaly.severity:.2f}"
    )
    if request.process is not None:
        lines.append(f"- 工艺：{request.process.name}（{request.process.operation}）")
    if request.equipment is not None:
        lines.append(f"- 设备：{request.equipment.name}（{request.equipment.type.value}）")

    lines.append("")
    lines.append("## 根因候选（已由确定性引擎排序，只能引用，不得重排或改置信度）")
    if request.root_causes:
        for candidate in request.root_causes:
            lines.append(f"- {candidate.cause}（置信度 {candidate.confidence:.0%}）")
    else:
        lines.append("- （无）")

    lines.append("")
    lines.append(f"## FMEA 匹配结果：{request.fmea_match.match_status.value}")
    for candidate in request.fmea_match.candidates:
        action = candidate.record.recommended_action or "（无既定措施）"
        lines.append(f"- {candidate.record.cause} → 既定措施：{action}")

    risk = request.risk
    lines.append("")
    lines.append("## 风险（确定性派生，不得修改）")
    lines.append(
        f"- S={risk.severity} O={risk.occurrence} D={risk.detection} "
        f"RPN={risk.rpn} AP={risk.ap.value}"
    )

    lines.append("")
    lines.append("## 证据（evidence_ids 只能取自此列表）")
    if request.evidence:
        for item in request.evidence:
            lines.append(
                f"- {item.id} | {item.type.value} | {item.source} | {item.value} "
                f"| relevance={item.relevance:.2f}"
            )
    else:
        lines.append("- （无）")

    lines.append("")
    lines.append("## 输出（只输出这一个 JSON 对象）")
    lines.append(_SCHEMA_HINT)
    return "\n".join(lines)


def _outermost_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else ""


def _extract_json_object(text: str) -> Mapping[str, Any]:
    """Parse the model's reply defensively: fences and stray prose are common."""
    stripped = text.strip()
    if stripped.startswith("```"):
        body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = body.rstrip()
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    for candidate in (stripped.strip(), _outermost_object(stripped)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            return parsed
    raise AiProviderError("AI response was not a JSON object")


def _extract_content(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise AiProviderError("AI response is not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AiProviderError("AI response has no 'choices'")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise AiProviderError("AI response 'choices[0]' is not an object")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise AiProviderError("AI response has no 'message'")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise AiProviderError("AI response 'content' is empty")
    return content


def _build_action(
    raw: object,
    *,
    index: int,
    known_evidence: set[str],
    priority: RiskLevel,
) -> RecommendedAction:
    if not isinstance(raw, Mapping):
        raise AiProviderError(f"recommended_actions[{index}] is not an object")
    action = raw.get("action")
    if not isinstance(action, str) or not action.strip():
        raise AiProviderError(f"recommended_actions[{index}].action is empty")
    rationale = raw.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        raise AiProviderError(f"recommended_actions[{index}].rationale is not a string")
    cited = raw.get("evidence_ids") or []
    if not isinstance(cited, list) or not all(isinstance(item, str) for item in cited):
        raise AiProviderError(
            f"recommended_actions[{index}].evidence_ids is not a list of strings"
        )
    unknown = sorted({item for item in cited if item not in known_evidence})
    if unknown:
        raise AiProviderError(
            "recommended action cites evidence that does not exist: " + ", ".join(unknown)
        )
    cleaned_rationale = rationale.strip() if isinstance(rationale, str) else ""
    return RecommendedAction(
        action=action.strip(),
        priority=priority,
        rationale=cleaned_rationale or None,
        evidence_ids=list(dict.fromkeys(cited)),
    )


def _build_result(
    payload: Mapping[str, Any],
    request: AiAnalysisRequest,
    *,
    provider: str,
    clock: Callable[[], datetime],
) -> AiAnalysisResult:
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise AiProviderError("AI response is missing a non-empty 'summary'")

    raw_actions = payload.get("recommended_actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise AiProviderError("AI response must contain at least one recommended action")

    known_evidence = {item.id for item in request.evidence}
    # Urgency comes from the deterministic AP, never from the model: an
    # explanation layer that can set its own urgency is scoring (spec §6.1).
    priority = AP_TO_RISK[request.risk.ap]
    actions = [
        _build_action(raw, index=index, known_evidence=known_evidence, priority=priority)
        for index, raw in enumerate(raw_actions)
    ]

    return AiAnalysisResult(
        summary=summary.strip(),
        root_cause_candidates=list(request.root_causes),
        risk=request.risk,
        recommended_actions=actions,
        provider=provider,
        generated_at=clock(),
    )


class OpenAiCompatibleAiProvider:
    """Explanation layer backed by any OpenAI-compatible chat-completions server."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        name: str = "openai-compatible",
        clock: Callable[[], datetime] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff: Callable[[int], float] | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("an OpenAI-compatible AI provider needs a base URL")
        if not model or not model.strip():
            raise ValueError("an OpenAI-compatible AI provider needs a model name")
        self.name = name
        self._model = model.strip()
        self._clock = clock or datetime.now
        self._max_retries = max(0, max_retries)
        self._backoff = backoff or _default_backoff
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def analyze(self, request: AiAnalysisRequest) -> AiAnalysisResult:
        content = await self._complete(request)
        payload = _extract_json_object(content)
        return _build_result(payload, request, provider=self.name, clock=self._clock)

    async def _complete(self, request: AiAnalysisRequest) -> str:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _fact_sheet(request)},
            ],
            "temperature": 0,
            "stream": False,
        }
        last_error: str | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post("chat/completions", json=body)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                raise AiProviderError(f"AI endpoint unreachable: {last_error}") from exc
            if response.status_code in _RETRY_STATUS and attempt < self._max_retries:
                last_error = f"HTTP {response.status_code}"
                await asyncio.sleep(self._backoff(attempt))
                continue
            if response.status_code >= 400:
                raise AiProviderError(
                    f"AI endpoint returned HTTP {response.status_code}: {response.text[:200]}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise AiProviderError("AI endpoint returned a non-JSON body") from exc
            return _extract_content(payload)
        raise AiProviderError(
            f"AI endpoint failed after {self._max_retries + 1} attempts: {last_error}"
        )