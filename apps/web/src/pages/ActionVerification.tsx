import type {
  ActionItemStatus,
  ActionWithContext,
  Anomaly,
  RiskLevel,
} from '@factorymind/shared-types'
import { useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { endpoints } from '../api/endpoints'
import { KpiCard, type KpiTone } from '../components/KpiCard'
import { Panel } from '../components/Panel'
import { useAsync } from '../hooks/useAsync'
import {
  formatDateTime,
  formatMetricValue,
  hoursAgo,
  metricLabel,
  metricUnit,
  pad,
  toIsoSeconds,
} from '../lib/format'
import { useAppStore } from '../store/useAppStore'

const WINDOW_HOURS = 8

const EMPTY_ACTIONS: ActionWithContext[] = []
const EMPTY_ANOMALIES: Anomaly[] = []

const STATUS_LABEL: Record<ActionItemStatus, string> = {
  OPEN: '待处理',
  IN_PROGRESS: '进行中',
  DONE: '已完成',
}

const STATUS_CHIP: Record<ActionItemStatus, string> = {
  OPEN: 'bg-panel-muted text-ink-muted',
  IN_PROGRESS: 'bg-warn-soft text-warn',
  DONE: 'bg-ok-soft text-ok',
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

/** Status flow is one-way; `DONE` is terminal until a re-verification reopens it. */
const NEXT_STATUS: Partial<Record<ActionItemStatus, ActionItemStatus>> = {
  OPEN: 'IN_PROGRESS',
  IN_PROGRESS: 'DONE',
}

const NEXT_LABEL: Partial<Record<ActionItemStatus, string>> = {
  OPEN: '开始处理',
  IN_PROGRESS: '标记完成',
}

const BTN_PRIMARY =
  'rounded border border-accent bg-accent px-2.5 py-1 text-[11px] font-medium text-white transition-colors hover:bg-accent-strong disabled:opacity-50'
const BTN_GHOST =
  'rounded border border-panel-line bg-panel px-2.5 py-1 text-[11px] font-medium text-ink-muted transition-colors hover:border-accent hover:text-accent disabled:opacity-50'
const FIELD =
  'w-full rounded border border-panel-line bg-panel px-2 py-1.5 text-[12px] text-ink outline-none focus:border-accent'

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatReading(value: number, unit: string): string {
  return `${formatMetricValue(value, unit)}${unit}`
}

export function ActionVerification() {
  const stations = useAppStore((state) => state.stations)
  const location = useLocation()
  const [reloadKey, setReloadKey] = useState(0)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ tone: KpiTone; text: string } | null>(null)

  const stationNames = useMemo(
    () => Object.fromEntries(stations.map((item) => [item.stationId, item.name])),
    [stations],
  )

  // Hand-off from the AI analysis page: it names the anomaly it wants actioned,
  // so the operator lands on a form that already points at the right fault.
  const initialAnomalyId = (location.state as { anomalyId?: string } | null)?.anomalyId ?? ''

  const timeWindow = useMemo(() => {
    const to = new Date()
    to.setSeconds(0, 0)
    return { from: toIsoSeconds(hoursAgo(WINDOW_HOURS)), to: toIsoSeconds(to) }
  }, [])

  const anomalyState = useAsync(
    () => endpoints.anomalies(timeWindow),
    [timeWindow.from, timeWindow.to],
  )
  const boardState = useAsync(() => endpoints.actions(), [reloadKey])

  const entries = boardState.data ?? EMPTY_ACTIONS
  const anomalies = anomalyState.data ?? EMPTY_ANOMALIES

  const counts = useMemo(() => {
    const open = entries.filter((item) => item.action.status === 'OPEN').length
    const active = entries.filter((item) => item.action.status === 'IN_PROGRESS').length
    const done = entries.filter((item) => item.action.status === 'DONE').length
    const verified = entries.filter((item) => item.latestVerification)
    const passed = verified.filter((item) => item.latestVerification?.passed).length
    return { open, active, done, verified: verified.length, passed }
  }, [entries])

  const passRate = counts.verified === 0 ? null : Math.round((counts.passed / counts.verified) * 100)

  async function advance(actionId: string, status: ActionItemStatus) {
    setBusyId(actionId)
    try {
      await endpoints.updateAction(actionId, { status })
      setReloadKey((key) => key + 1)
    } catch (error) {
      setNotice({ tone: 'crit', text: messageOf(error) })
    } finally {
      setBusyId(null)
    }
  }

  async function verify(actionId: string) {
    setBusyId(actionId)
    try {
      const result = await endpoints.verifyAction(actionId)
      const { verification } = result
      const unit = metricUnit(verification.metric)
      const reading = `${formatReading(verification.before, unit)} → ${formatReading(
        verification.after,
        unit,
      )}`
      setNotice(
        verification.passed
          ? {
              tone: 'ok',
              text: `验证通过：${metricLabel(verification.metric)} ${reading}，已回到目标带（目标 ${formatReading(
                verification.target,
                unit,
              )}）。`,
            }
          : {
              tone: 'crit',
              text: `验证未通过：${metricLabel(verification.metric)} 当前 ${formatReading(
                verification.after,
                unit,
              )}，尚未回到目标 ${formatReading(
                verification.target,
                unit,
              )}，行动保持开启。`,
            },
      )
      setReloadKey((key) => key + 1)
    } catch (error) {
      setNotice({ tone: 'crit', text: messageOf(error) })
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="待处理"
          value={String(counts.open)}
          unit="项"
          hint="尚未开始"
          tone={counts.open === 0 ? 'ok' : 'warn'}
        />
        <KpiCard label="进行中" value={String(counts.active)} unit="项" hint="已派工" />
        <KpiCard
          label="已完成"
          value={String(counts.done)}
          unit="项"
          hint="已关闭或验证通过"
          tone={counts.done === 0 ? 'neutral' : 'ok'}
        />
        <KpiCard
          label="验证通过率"
          value={passRate === null ? '—' : String(passRate)}
          unit={passRate === null ? undefined : '%'}
          hint={
            counts.verified === 0
              ? '尚无验证记录'
              : `${counts.passed} / ${counts.verified} 项达标`
          }
          tone={passRate === null ? 'neutral' : passRate >= 80 ? 'ok' : 'crit'}
        />
      </div>

      {notice && (
        <div
          className={`flex items-start justify-between gap-3 rounded border px-4 py-2.5 text-[12px] ${
            notice.tone === 'ok'
              ? 'border-ok/30 bg-ok-soft text-ok'
              : 'border-crit/30 bg-crit-soft text-crit'
          }`}
        >
          <span>{notice.text}</span>
          <button
            type="button"
            onClick={() => setNotice(null)}
            className="shrink-0 text-[11px] opacity-70 hover:opacity-100"
          >
            关闭
          </button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="行动项"
          subtitle="措施 → 责任人 → 截止 → 状态 → 效果验证"
        >
          {boardState.loading && (
            <p className="py-8 text-center text-[12px] text-ink-faint">加载中…</p>
          )}
          {!boardState.loading && boardState.error && (
            <p className="py-8 text-center text-[12px] text-crit">
              加载失败：{boardState.error}
            </p>
          )}
          {!boardState.loading && !boardState.error && entries.length === 0 && (
            <p className="py-8 text-center text-[12px] text-ink-faint">
              尚无行动项。在右侧选择异常并创建改善行动。
            </p>
          )}

          {!boardState.loading && !boardState.error && entries.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="border-b border-panel-line text-left text-[11px] text-ink-muted">
                    <th className="py-2 pr-3 font-medium">行动</th>
                    <th className="py-2 pr-3 font-medium">关联异常</th>
                    <th className="py-2 pr-3 font-medium">责任人</th>
                    <th className="py-2 pr-3 font-medium">截止</th>
                    <th className="py-2 pr-3 font-medium">优先级</th>
                    <th className="py-2 pr-3 font-medium">状态</th>
                    <th className="py-2 pr-3 font-medium">效果验证</th>
                    <th className="py-2 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry) => {
                    const { action, anomaly, latestVerification } = entry
                    const unit = metricUnit(anomaly.metric)
                    const next = NEXT_STATUS[action.status]
                    const busy = busyId === action.id
                    return (
                      <tr
                        key={action.id}
                        className="border-b border-panel-line/70 align-top"
                      >
                        <td className="py-2.5 pr-3">
                          <div className="font-medium text-ink">{action.title}</div>
                          <div className="mt-0.5 font-mono text-[10px] text-ink-faint">
                            {action.id}
                          </div>
                        </td>
                        <td className="py-2.5 pr-3">
                          <div className="text-ink">
                            {stationNames[anomaly.stationId] ?? anomaly.stationId}
                          </div>
                          <div className="mt-0.5 text-[11px] text-ink-faint">
                            {metricLabel(anomaly.metric)} ·{' '}
                            <span className="font-mono">{anomaly.stationId}</span>
                          </div>
                        </td>
                        <td className="py-2.5 pr-3 text-ink-muted">{action.owner}</td>
                        <td className="tabular py-2.5 pr-3 text-ink-muted">
                          {formatDateTime(action.dueDate)}
                        </td>
                        <td className="py-2.5 pr-3">
                          <span
                            className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium ${
                              PRIORITY_CHIP[action.priority]
                            }`}
                          >
                            {PRIORITY_LABEL[action.priority]}
                          </span>
                        </td>
                        <td className="py-2.5 pr-3">
                          <span
                            className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium ${
                              STATUS_CHIP[action.status]
                            }`}
                          >
                            {STATUS_LABEL[action.status]}
                          </span>
                        </td>
                        <td className="tabular py-2.5 pr-3">
                          {latestVerification ? (
                            <div className="flex items-center gap-2">
                              <span className="text-ink">
                                {formatReading(latestVerification.before, unit)}
                                <span className="mx-1 text-ink-faint">→</span>
                                {formatReading(latestVerification.after, unit)}
                              </span>
                              <span
                                className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                                  latestVerification.passed
                                    ? 'bg-ok-soft text-ok'
                                    : 'bg-crit-soft text-crit'
                                }`}
                              >
                                {latestVerification.passed ? '达标' : '未达标'}
                              </span>
                            </div>
                          ) : (
                            <span className="text-ink-faint">未验证</span>
                          )}
                        </td>
                        <td className="py-2.5">
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => void verify(action.id)}
                              disabled={busy}
                              className={BTN_PRIMARY}
                            >
                              验证
                            </button>
                            {next && (
                              <button
                                type="button"
                                onClick={() => void advance(action.id, next)}
                                disabled={busy}
                                className={BTN_GHOST}
                              >
                                {NEXT_LABEL[action.status]}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          <p className="mt-3 text-[10px] leading-relaxed text-ink-faint">
            验证为确定性重测：引擎在行动后窗口重新读取该指标的中位水平，与目标带比较后给出达标结论，AI 不参与判定。
          </p>
        </Panel>

        <ActionComposer
          anomalies={anomalies}
          stationNames={stationNames}
          loading={anomalyState.loading}
          initialAnomalyId={initialAnomalyId}
          onCreated={() => {
            setReloadKey((key) => key + 1)
            setNotice(null)
          }}
        />
      </div>
    </div>
  )
}

interface ActionComposerProps {
  anomalies: Anomaly[]
  stationNames: Record<string, string>
  loading: boolean
  initialAnomalyId?: string
  onCreated: () => void
}

function ActionComposer({
  anomalies,
  stationNames,
  loading,
  initialAnomalyId = '',
  onCreated,
}: ActionComposerProps) {
  const [anomalyId, setAnomalyId] = useState(initialAnomalyId)
  const [title, setTitle] = useState('')
  const [owner, setOwner] = useState('')
  const [dueDate, setDueDate] = useState(defaultDueDate)
  const [priority, setPriority] = useState<RiskLevel>('MEDIUM')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const anomaly = anomalies.find((item) => item.id === anomalyId) ?? null

  // The recommendation comes from the same /api/analysis the Assess page uses,
  // so accepting it here is the Page 5 → Page 6 hand-off, not a second opinion.
  const analysis = useAsync(
    () => (anomaly ? endpoints.analysis({ anomaly }) : Promise.resolve(null)),
    [anomalyId],
  )
  const recommendations = analysis.data?.analysis.recommendedActions ?? []

  function pickAnomaly(id: string) {
    setAnomalyId(id)
    setError(null)
    const picked = anomalies.find((item) => item.id === id)
    if (picked) {
      setPriority(picked.severity >= 0.7 ? 'HIGH' : picked.severity >= 0.4 ? 'MEDIUM' : 'LOW')
    }
  }

  async function submit() {
    if (!anomaly) {
      setError('请先选择要处理的异常。')
      return
    }
    if (!title.trim()) {
      setError('请填写行动标题。')
      return
    }
    if (!owner.trim()) {
      setError('请填写责任人。')
      return
    }

    setBusy(true)
    setError(null)
    try {
      await endpoints.createAction({
        anomaly,
        title: title.trim(),
        owner: owner.trim(),
        dueDate,
        priority,
      })
      setAnomalyId('')
      setTitle('')
      setOwner('')
      setDueDate(defaultDueDate())
      onCreated()
    } catch (submitError) {
      setError(messageOf(submitError))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="新建行动" subtitle="从异常发起，接受 AI 建议措施">
      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-muted">关联异常</span>
          <select
            value={anomalyId}
            onChange={(event) => pickAnomaly(event.target.value)}
            disabled={loading || anomalies.length === 0}
            className={FIELD}
          >
            <option value="">
              {loading ? '检测中…' : anomalies.length === 0 ? '当前窗口无异常' : '请选择异常'}
            </option>
            {anomalies.map((item) => (
              <option key={item.id} value={item.id}>
                {stationNames[item.stationId] ?? item.stationId} · {metricLabel(item.metric)} ·{' '}
                {item.severity.toFixed(2)}
              </option>
            ))}
          </select>
        </label>

        {anomaly && (
          <div className="rounded border border-panel-line bg-panel-muted px-3 py-2">
            <div className="text-[10px] text-ink-muted">AI 建议措施</div>
            {analysis.loading && (
              <p className="mt-1 text-[11px] text-ink-faint">生成中…</p>
            )}
            {!analysis.loading && recommendations.length === 0 && (
              <p className="mt-1 text-[11px] text-ink-faint">暂无建议措施。</p>
            )}
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {recommendations.map((item) => (
                <button
                  key={item.action}
                  type="button"
                  onClick={() => setTitle(item.action)}
                  title={item.rationale ?? undefined}
                  className="rounded border border-accent/40 bg-accent-soft px-2 py-0.5 text-left text-[11px] text-accent-strong transition-colors hover:border-accent"
                >
                  {item.action}
                </button>
              ))}
            </div>
          </div>
        )}

        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-muted">行动标题</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="例如：复核拧紧枪扭矩标定"
            className={FIELD}
          />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="mb-1 block text-[11px] text-ink-muted">责任人</span>
            <input
              value={owner}
              onChange={(event) => setOwner(event.target.value)}
              placeholder="姓名 / 工号"
              className={FIELD}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[11px] text-ink-muted">优先级</span>
            <select
              value={priority}
              onChange={(event) => setPriority(event.target.value as RiskLevel)}
              className={FIELD}
            >
              <option value="HIGH">高</option>
              <option value="MEDIUM">中</option>
              <option value="LOW">低</option>
            </select>
          </label>
        </div>

        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-muted">截止时间</span>
          <input
            type="datetime-local"
            value={dueDate}
            onChange={(event) => setDueDate(event.target.value)}
            className={FIELD}
          />
        </label>

        {error && <p className="text-[11px] text-crit">{error}</p>}

        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || anomalies.length === 0}
          className={`${BTN_PRIMARY} w-full py-1.5`}
        >
          {busy ? '创建中…' : '创建行动'}
        </button>
      </div>
    </Panel>
  )
}

function defaultDueDate(): string {
  const due = new Date(Date.now() + 3 * 24 * 3600_000)
  return `${due.getFullYear()}-${pad(due.getMonth() + 1)}-${pad(due.getDate())}T17:00`
}