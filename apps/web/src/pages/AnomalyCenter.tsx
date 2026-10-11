import type { Anomaly, AnomalyStatus } from '@factorymind/shared-types'
import { Fragment, useMemo, useState } from 'react'
import { endpoints } from '../api/endpoints'
import { KpiCard, type KpiTone } from '../components/KpiCard'
import { Panel } from '../components/Panel'
import { useAsync } from '../hooks/useAsync'
import { formatDateTime, hoursAgo, metricLabel, metricUnit, toIsoSeconds } from '../lib/format'
import { useAppStore } from '../store/useAppStore'

const WINDOW_HOURS = 8

const EMPTY: Anomaly[] = []

const STATUS_LABEL: Record<AnomalyStatus, string> = {
  NEW: '新建',
  ANALYZING: '分析中',
  CONFIRMED: '已确认',
  ACTION_CREATED: '已建行动',
  RESOLVED: '已解决',
}

const HIGH_SEVERITY = 0.7
const MEDIUM_SEVERITY = 0.4

interface Band {
  label: string
  chip: string
  tone: KpiTone
}

function band(severity: number): Band {
  if (severity >= HIGH_SEVERITY) {
    return { label: '高', chip: 'bg-crit-soft text-crit', tone: 'crit' }
  }
  if (severity >= MEDIUM_SEVERITY) {
    return { label: '中', chip: 'bg-warn-soft text-warn', tone: 'warn' }
  }
  return { label: '低', chip: 'bg-accent-soft text-accent-strong', tone: 'ok' }
}

export function AnomalyCenter() {
  const stations = useAppStore((state) => state.stations)
  const lines = useAppStore((state) => state.lines)
  const [openId, setOpenId] = useState<string | null>(null)

  const stationNames = useMemo(
    () => Object.fromEntries(stations.map((item) => [item.stationId, item.name])),
    [stations],
  )
  const lineNames = useMemo(
    () => Object.fromEntries(lines.map((item) => [item.lineId, item.name])),
    [lines],
  )

  const timeWindow = useMemo(() => {
    const to = new Date()
    to.setSeconds(0, 0)
    return { from: toIsoSeconds(hoursAgo(WINDOW_HOURS)), to: toIsoSeconds(to) }
  }, [])

  // Factory-wide on purpose: an anomaly center should rank stations against each
  // other, so the header's line selector is not applied here.
  const { data, loading, error } = useAsync(
    () => endpoints.anomalies(timeWindow),
    [timeWindow.from, timeWindow.to],
  )

  const anomalies = data ?? EMPTY
  const highRisk = anomalies.filter((item) => item.severity >= HIGH_SEVERITY)
  const affectedStations = new Set(anomalies.map((item) => item.stationId))
  const peak = anomalies[0]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="活跃异常"
          value={String(anomalies.length)}
          unit="条"
          hint={`近 ${WINDOW_HOURS} 小时 · 全厂`}
          tone={anomalies.length === 0 ? 'ok' : 'neutral'}
        />
        <KpiCard
          label="高危异常"
          value={String(highRisk.length)}
          unit="条"
          hint={`严重度 ≥ ${HIGH_SEVERITY.toFixed(1)}`}
          tone={highRisk.length === 0 ? 'ok' : 'crit'}
        />
        <KpiCard
          label="涉及工位"
          value={String(affectedStations.size)}
          unit="个"
          hint={`共 ${stations.length} 个工位在监`}
          tone={affectedStations.size === 0 ? 'ok' : 'warn'}
        />
        <KpiCard
          label="最高严重度"
          value={peak ? peak.severity.toFixed(2) : '—'}
          hint={peak ? `${stationNames[peak.stationId] ?? peak.stationId}` : '窗口内无异常'}
          tone={peak ? band(peak.severity).tone : 'ok'}
        />
      </div>

      <Panel
        title="异常列表"
        subtitle={`Z-score 规则检出 · 按严重度排序 · 点击行查看证据链`}
      >
        {loading && <p className="py-8 text-center text-[12px] text-ink-faint">检测中…</p>}
        {!loading && error && (
          <p className="py-8 text-center text-[12px] text-crit">加载失败：{error}</p>
        )}
        {!loading && !error && anomalies.length === 0 && (
          <p className="py-8 text-center text-[12px] text-ink-faint">
            当前时间窗内未检出异常。
          </p>
        )}

        {!loading && !error && anomalies.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-b border-panel-line text-left text-[11px] text-ink-muted">
                  <th className="py-2 pr-3 font-medium">严重度</th>
                  <th className="py-2 pr-3 font-medium">发生时间</th>
                  <th className="py-2 pr-3 font-medium">产线 / 工位</th>
                  <th className="py-2 pr-3 font-medium">指标</th>
                  <th className="py-2 pr-3 text-right font-medium">基线 → 实际</th>
                  <th className="py-2 pr-3 text-right font-medium">偏离</th>
                  <th className="py-2 pr-3 text-right font-medium">Z</th>
                  <th className="py-2 font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {anomalies.map((anomaly) => {
                  const severityBand = band(anomaly.severity)
                  const unit = metricUnit(anomaly.metric)
                  const isOpen = anomaly.id === openId
                  return (
                    <Fragment key={anomaly.id}>
                      <tr
                        onClick={() => setOpenId(isOpen ? null : anomaly.id)}
                        className={`cursor-pointer border-b border-panel-line/70 transition-colors ${
                          isOpen ? 'bg-panel-muted' : 'hover:bg-panel-muted/60'
                        }`}
                      >
                        <td className="py-2 pr-3">
                          <span
                            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ${severityBand.chip}`}
                          >
                            {severityBand.label}
                            <span className="tabular font-normal opacity-80">
                              {anomaly.severity.toFixed(2)}
                            </span>
                          </span>
                        </td>
                        <td className="tabular py-2 pr-3 text-ink-muted">
                          {formatDateTime(anomaly.timestamp)}
                        </td>
                        <td className="py-2 pr-3">
                          <div className="font-medium text-ink">
                            {stationNames[anomaly.stationId] ?? anomaly.stationId}
                          </div>
                          <div className="text-[11px] text-ink-faint">
                            {lineNames[anomaly.lineId] ?? anomaly.lineId} ·{' '}
                            <span className="font-mono">{anomaly.stationId}</span>
                          </div>
                        </td>
                        <td className="py-2 pr-3 text-ink-muted">
                          {metricLabel(anomaly.metric)}
                        </td>
                        <td className="tabular py-2 pr-3 text-right">
                          <span className="text-ink-faint">
                            {anomaly.baseline.toFixed(2)}
                            {unit}
                          </span>
                          <span className="mx-1 text-ink-faint">→</span>
                          <span className="font-medium text-ink">
                            {anomaly.actual.toFixed(2)}
                            {unit}
                          </span>
                        </td>
                        <td
                          className={`tabular py-2 pr-3 text-right font-medium ${
                            anomaly.delta < 0 ? 'text-crit' : 'text-warn'
                          }`}
                        >
                          {anomaly.delta >= 0 ? '↑' : '↓'}{' '}
                          {Math.abs(anomaly.deltaRatio * 100).toFixed(1)}%
                        </td>
                        <td className="tabular py-2 pr-3 text-right text-ink-muted">
                          {anomaly.zScore.toFixed(1)}
                        </td>
                        <td className="py-2 text-ink-muted">
                          {STATUS_LABEL[anomaly.status]}
                        </td>
                      </tr>

                      {isOpen && (
                        <tr className="border-b border-panel-line/70">
                          <td colSpan={8} className="bg-panel-muted px-4 py-3">
                            <EvidenceList anomaly={anomaly} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}

function EvidenceList({ anomaly }: { anomaly: Anomaly }) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[12px] font-semibold text-ink">证据链</h3>
        <span className="font-mono text-[10px] text-ink-faint">{anomaly.id}</span>
      </div>
      <ul className="space-y-1.5">
        {anomaly.evidence.map((item) => (
          <li key={item.id} className="flex items-center gap-3">
            <span className="w-28 shrink-0 truncate text-[11px] text-ink-muted">
              {item.source}
            </span>
            <span className="tabular flex-1 text-[12px] text-ink">{String(item.value)}</span>
            <span className="flex w-24 shrink-0 items-center gap-1.5">
              <span className="h-1 flex-1 overflow-hidden rounded-full bg-panel-line">
                <span
                  className="block h-full rounded-full bg-accent"
                  style={{ width: `${Math.round(item.relevance * 100)}%` }}
                />
              </span>
              <span className="tabular w-8 text-right text-[10px] text-ink-faint">
                {item.relevance.toFixed(2)}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-[10px] text-ink-faint">
        证据为同窗口内的统计相关性，用于排序与解释，不构成根因结论。
      </p>
    </div>
  )
}