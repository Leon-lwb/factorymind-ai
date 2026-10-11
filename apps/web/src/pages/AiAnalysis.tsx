import type {
  Anomaly,
  EvidenceType,
  FmeaMatchStatus,
  FourMOneECategory,
  RiskLevel,
} from '@factorymind/shared-types'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { endpoints } from '../api/endpoints'
import { Panel } from '../components/Panel'
import { useAsync } from '../hooks/useAsync'
import {
  formatDateTime,
  formatMetricValue,
  hoursAgo,
  metricLabel,
  metricUnit,
  toIsoSeconds,
} from '../lib/format'
import { useAppStore } from '../store/useAppStore'

const WINDOW_HOURS = 8

const EMPTY: Anomaly[] = []

const HIGH_SEVERITY = 0.7
const MEDIUM_SEVERITY = 0.4

const MATCH_LABEL: Record<FmeaMatchStatus, string> = {
  EXACT: '精确匹配',
  NORMALIZED: '规范化匹配',
  SEMANTIC: '语义匹配',
  LLM_CANDIDATE: 'LLM 候选',
  NO_MATCH: '无匹配',
}

const MATCH_CHIP: Record<FmeaMatchStatus, string> = {
  EXACT: 'bg-ok-soft text-ok',
  NORMALIZED: 'bg-ok-soft text-ok',
  SEMANTIC: 'bg-warn-soft text-warn',
  LLM_CANDIDATE: 'bg-warn-soft text-warn',
  NO_MATCH: 'bg-crit-soft text-crit',
}

const CATEGORY_LABEL: Record<FourMOneECategory, string> = {
  MAN: '人',
  MACHINE: '机',
  MATERIAL: '料',
  METHOD: '法',
  ENVIRONMENT: '环',
}

const EVIDENCE_LABEL: Record<EvidenceType, string> = {
  METRIC_SHIFT: '指标突变',
  EVENT_CORRELATION: '事件关联',
  HISTORICAL_PATTERN: '历史模式',
  PROCESS_CONTEXT: '工艺上下文',
}

const PRIORITY_LABEL: Record<RiskLevel, string> = {
  LOW: '低',
  MEDIUM: '中',
  HIGH: '高',
}

const PRIORITY_CHIP: Record<RiskLevel, string> = {
  LOW: 'bg-accent-soft text-accent-strong',
  MEDIUM: 'bg-warn-soft text-warn',
  HIGH: 'bg-crit-soft text-crit',
}

const BTN_PRIMARY =
  'rounded border border-accent bg-accent px-2.5 py-1 text-[11px] font-medium text-white transition-colors hover:bg-accent-strong disabled:opacity-50'

function severityChip(severity: number): { label: string; chip: string } {
  if (severity >= HIGH_SEVERITY) return { label: '高', chip: 'bg-crit-soft text-crit' }
  if (severity >= MEDIUM_SEVERITY) return { label: '中', chip: 'bg-warn-soft text-warn' }
  return { label: '低', chip: 'bg-accent-soft text-accent-strong' }
}

function reading(value: number, unit: string): string {
  return `${formatMetricValue(value, unit)}${unit}`
}

export function AiAnalysis() {
  const stations = useAppStore((state) => state.stations)
  const navigate = useNavigate()
  const [pickedId, setPickedId] = useState<string | null>(null)

  const stationNames = useMemo(
    () => Object.fromEntries(stations.map((item) => [item.stationId, item.name])),
    [stations],
  )

  const timeWindow = useMemo(() => {
    const to = new Date()
    to.setSeconds(0, 0)
    return { from: toIsoSeconds(hoursAgo(WINDOW_HOURS)), to: toIsoSeconds(to) }
  }, [])

  const anomalyState = useAsync(
    () => endpoints.anomalies(timeWindow),
    [timeWindow.from, timeWindow.to],
  )
  const anomalies = anomalyState.data ?? EMPTY

  // Highest-severity anomaly leads until the operator picks another.
  const selected = anomalies.find((item) => item.id === pickedId) ?? anomalies[0] ?? null

  const analysisState = useAsync(
    () => (selected ? endpoints.analysis({ anomaly: selected }) : Promise.resolve(null)),
    [selected?.id],
  )
  const result = analysisState.data
  const analysis = result?.analysis ?? null
  const match = result?.fmeaMatch ?? null

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel
        title="待分析异常"
        subtitle={`近 ${WINDOW_HOURS} 小时 · 按严重度排序`}
        className="lg:col-span-1"
      >
        {anomalyState.loading && (
          <p className="py-8 text-center text-[12px] text-ink-faint">检测中…</p>
        )}
        {!anomalyState.loading && anomalyState.error && (
          <p className="py-8 text-center text-[12px] text-crit">
            加载失败：{anomalyState.error}
          </p>
        )}
        {!anomalyState.loading && !anomalyState.error && anomalies.length === 0 && (
          <p className="py-8 text-center text-[12px] text-ink-faint">
            当前时间窗内未检出异常。
          </p>
        )}

        <ul className="space-y-1.5">
          {anomalies.map((anomaly) => {
            const band = severityChip(anomaly.severity)
            const unit = metricUnit(anomaly.metric)
            const isActive = anomaly.id === selected?.id
            return (
              <li key={anomaly.id}>
                <button
                  type="button"
                  onClick={() => setPickedId(anomaly.id)}
                  className={`w-full rounded border px-2.5 py-2 text-left transition-colors ${
                    isActive
                      ? 'border-accent bg-accent-soft'
                      : 'border-panel-line bg-panel hover:border-accent/50'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[12px] font-medium text-ink">
                      {stationNames[anomaly.stationId] ?? anomaly.stationId}
                    </span>
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${band.chip}`}
                    >
                      {band.label} {anomaly.severity.toFixed(2)}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-ink-faint">
                    {metricLabel(anomaly.metric)}
                  </div>
                  <div className="tabular mt-0.5 text-[11px] text-ink-muted">
                    {reading(anomaly.baseline, unit)}
                    <span className="mx-1 text-ink-faint">→</span>
                    <span className="font-medium text-ink">{reading(anomaly.actual, unit)}</span>
                  </div>
                  <div className="tabular mt-0.5 text-[10px] text-ink-faint">
                    {formatDateTime(anomaly.timestamp)}
                  </div>
                </button>
              </li>
            )
          })}
        </ul>
      </Panel>

      <div className="space-y-4 lg:col-span-2">
        <Panel
          title="AI 分析结论"
          subtitle="基于证据的推断 · 非确定性结论"
          actions={
            analysis && (
              <span className="rounded bg-warn-soft px-1.5 py-0.5 text-[10px] font-medium text-warn">
                非确定性判断
              </span>
            )
          }
        >
          {!selected && (
            <p className="py-8 text-center text-[12px] text-ink-faint">
              选择左侧异常以生成分析。
            </p>
          )}
          {selected && analysisState.loading && (
            <p className="py-8 text-center text-[12px] text-ink-faint">推理中…</p>
          )}
          {selected && !analysisState.loading && analysisState.error && (
            <p className="py-8 text-center text-[12px] text-crit">
              分析失败：{analysisState.error}
            </p>
          )}

          {selected && analysis && match && (
            <div className="space-y-3">
              <p className="border-l-2 border-accent pl-3 text-[13px] leading-relaxed text-ink">
                {analysis.summary}
              </p>

              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded border border-panel-line bg-panel-muted px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-ink-muted">FMEA 匹配</span>
                  <span
                    className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                      MATCH_CHIP[match.matchStatus]
                    }`}
                  >
                    {MATCH_LABEL[match.matchStatus]}
                  </span>
                  {match.requiresHumanReview && (
                    <span className="text-[10px] text-warn">需人工复核</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-ink-muted">风险 RPN</span>
                  <span className="tabular text-[12px] font-semibold text-ink">
                    {analysis.risk.rpn}
                  </span>
                  <span className="text-[10px] text-ink-muted">
                    S{analysis.risk.severity} / O{analysis.risk.occurrence} / D
                    {analysis.risk.detection}
                  </span>
                  <span className="rounded bg-panel px-1.5 py-0.5 text-[10px] font-medium text-ink-muted">
                    AP {analysis.risk.ap}
                  </span>
                </div>
                <div className="ml-auto flex items-center gap-2 text-[10px] text-ink-faint">
                  <span>provider: {analysis.provider}</span>
                  <span className="tabular">{formatDateTime(analysis.generatedAt)}</span>
                </div>
              </div>

              <button
                type="button"
                className={BTN_PRIMARY}
                onClick={() =>
                  void navigate('/actions', { state: { anomalyId: selected.id } })
                }
              >
                以此异常创建行动
              </button>
            </div>
          )}
        </Panel>

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="根因候选" subtitle="按证据加权排序 · 置信度非概率保证">
            {!analysis && <p className="py-6 text-center text-[12px] text-ink-faint">—</p>}
            {analysis && analysis.rootCauseCandidates.length === 0 && (
              <p className="py-6 text-center text-[12px] text-ink-faint">
                无匹配 FMEA 记录，无法给出根因候选，需人工确认。
              </p>
            )}
            {analysis && analysis.rootCauseCandidates.length > 0 && (
              <ol className="space-y-3">
                {analysis.rootCauseCandidates.map((candidate, index) => (
                  <li key={candidate.cause}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex min-w-0 items-start gap-2">
                        <span className="tabular mt-0.5 text-[11px] text-ink-faint">
                          #{index + 1}
                        </span>
                        <span className="text-[12px] font-medium text-ink">
                          {candidate.cause}
                        </span>
                        {candidate.category && (
                          <span className="mt-0.5 shrink-0 rounded bg-panel-muted px-1.5 py-0.5 text-[10px] text-ink-muted">
                            {CATEGORY_LABEL[candidate.category]}
                          </span>
                        )}
                      </div>
                      <span className="tabular shrink-0 text-[12px] font-semibold text-ink">
                        {Math.round(candidate.confidence * 100)}%
                      </span>
                    </div>
                    <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-panel-line">
                      <div
                        className="h-full rounded-full bg-accent"
                        style={{ width: `${Math.round(candidate.confidence * 100)}%` }}
                      />
                    </div>
                    <div className="mt-1 text-[10px] text-ink-faint">
                      {candidate.evidenceIds.length > 0
                        ? `支撑证据：${candidate.evidenceIds.join('、')}`
                        : '无直接支撑证据'}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Panel>

          <Panel title="证据链" subtitle="同窗口统计相关性 · 用于排序与解释">
            {!selected && <p className="py-6 text-center text-[12px] text-ink-faint">—</p>}
            {selected && (
              <>
                <ul className="space-y-2">
                  {selected.evidence.map((item) => (
                    <li key={item.id} className="flex items-start gap-2.5">
                      <span className="w-14 shrink-0 pt-0.5 text-[10px] text-ink-faint">
                        {EVIDENCE_LABEL[item.type]}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[11px] text-ink-muted">
                          {item.source}
                        </span>
                        <span className="tabular block text-[12px] text-ink">
                          {String(item.value)}
                        </span>
                      </span>
                      <span className="flex w-20 shrink-0 items-center gap-1.5 pt-1">
                        <span className="h-1 flex-1 overflow-hidden rounded-full bg-panel-line">
                          <span
                            className="block h-full rounded-full bg-accent"
                            style={{ width: `${Math.round(item.relevance * 100)}%` }}
                          />
                        </span>
                        <span className="tabular w-7 text-right text-[10px] text-ink-faint">
                          {item.relevance.toFixed(2)}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="mt-3 text-[10px] leading-relaxed text-ink-faint">
                  证据为同窗口内的统计相关性，不构成根因结论。
                </p>
              </>
            )}
          </Panel>
        </div>

        <Panel title="推荐措施" subtitle="来自匹配 FMEA 的既定措施">
          {!analysis && <p className="py-6 text-center text-[12px] text-ink-faint">—</p>}
          {analysis && (
            <ul className="space-y-2">
              {analysis.recommendedActions.map((item) => (
                <li
                  key={item.action}
                  className="flex items-start justify-between gap-3 rounded border border-panel-line bg-panel-muted px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="text-[12px] text-ink">{item.action}</div>
                    {item.rationale && (
                      <div className="mt-0.5 text-[10px] text-ink-faint">{item.rationale}</div>
                    )}
                  </div>
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                      PRIORITY_CHIP[item.priority]
                    }`}
                  >
                    {PRIORITY_LABEL[item.priority]}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  )
}