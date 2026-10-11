import type {
  ActionPriority,
  FmeaMatchRequest,
  FmeaMatchStatus,
  FmeaRecord,
  FourMOneECategory,
} from '@factorymind/shared-types'
import { useMemo, useState } from 'react'
import { endpoints } from '../api/endpoints'
import { KpiCard } from '../components/KpiCard'
import { Panel } from '../components/Panel'
import { useAsync } from '../hooks/useAsync'

const EMPTY: FmeaRecord[] = []

const SCORES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

const AP_LABEL: Record<ActionPriority, string> = { H: '高', M: '中', L: '低' }

const AP_CHIP: Record<ActionPriority, string> = {
  H: 'bg-crit-soft text-crit',
  M: 'bg-warn-soft text-warn',
  L: 'bg-accent-soft text-accent-strong',
}

const AP_CELL: Record<ActionPriority, string> = {
  H: 'bg-crit-soft text-crit font-semibold',
  M: 'bg-warn-soft text-warn font-medium',
  L: 'bg-accent-soft text-accent-strong',
}

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

/**
 * One preset per match level, so the four-step degradation can be walked through
 * in order instead of typed. `NO_MATCH` is included on purpose: the honest miss
 * is a feature of the engine, not an edge case to hide (spec §8.2).
 */
const PRESETS: { label: string; body: FmeaMatchRequest }[] = [
  { label: '精确命中', body: { failureMode: '一次合格率下降', process: '关键扭矩拧紧' } },
  { label: '规范化降级', body: { failureMode: '一次合格率下降。', process: '关键扭矩拧紧' } },
  { label: '语义降级', body: { failureMode: '一次合格率降低', process: '关键扭矩拧紧' } },
  { label: '无匹配', body: { failureMode: '焊缝气孔率上升', process: '车身焊接' } },
]

const FIELD =
  'w-full rounded border border-panel-line bg-panel px-2 py-1.5 text-[12px] text-ink outline-none focus:border-accent'

const BTN_GHOST =
  'rounded border border-panel-line bg-panel px-2 py-1 text-[11px] font-medium text-ink-muted transition-colors hover:border-accent hover:text-accent'

const BTN_PRIMARY =
  'rounded border border-accent bg-accent px-3 py-1.5 text-[11px] font-medium text-white transition-colors hover:bg-accent-strong'

interface MatrixCell {
  count: number
  ap: ActionPriority
}

const AP_RANK: Record<ActionPriority, number> = { L: 0, M: 1, H: 2 }

export function FmeaRisk() {
  const recordState = useAsync(() => endpoints.fmeaRecords(), [])
  const records = recordState.data ?? EMPTY

  const [form, setForm] = useState<FmeaMatchRequest>({ failureMode: '', process: '', operation: '' })
  const [query, setQuery] = useState<FmeaMatchRequest | null>(null)
  const [confirmedKey, setConfirmedKey] = useState<string | null>(null)

  const matchState = useAsync(
    () => (query ? endpoints.fmeaMatch(query) : Promise.resolve(null)),
    [query],
  )
  const match = matchState.data

  const stats = useMemo(() => {
    const high = records.filter((item) => item.ap === 'H').length
    const medium = records.filter((item) => item.ap === 'M').length
    const processes = new Set(records.map((item) => item.process))
    return { high, medium, processes: processes.size }
  }, [records])

  const sortedRecords = useMemo(
    () => [...records].sort((left, right) => right.rpn - left.rpn || left.cause.localeCompare(right.cause)),
    [records],
  )

  const failureModes = useMemo(
    () => Array.from(new Set(records.map((item) => item.failureMode))),
    [records],
  )

  // Severity × occurrence, the classic FMEA matrix. A cell takes the *worst* AP
  // among the rows that land on it — one severe row must not be averaged away.
  const matrix = useMemo(() => {
    const cells = new Map<string, MatrixCell>()
    for (const record of records) {
      const key = `${record.severity}:${record.occurrence}`
      const current = cells.get(key)
      if (!current) {
        cells.set(key, { count: 1, ap: record.ap })
      } else {
        current.count += 1
        if (AP_RANK[record.ap] > AP_RANK[current.ap]) current.ap = record.ap
      }
    }
    return cells
  }, [records])

  function submit(body: FmeaMatchRequest) {
    setConfirmedKey(null)
    setQuery({ ...body })
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="FMEA 记录"
          value={String(records.length)}
          unit="条"
          hint="合成 Master FMEA"
        />
        <KpiCard
          label="高行动优先级"
          value={String(stats.high)}
          unit="条"
          hint="AP = H"
          tone={stats.high === 0 ? 'ok' : 'crit'}
        />
        <KpiCard
          label="中行动优先级"
          value={String(stats.medium)}
          unit="条"
          hint="AP = M"
          tone={stats.medium === 0 ? 'ok' : 'warn'}
        />
        <KpiCard
          label="覆盖工艺"
          value={String(stats.processes)}
          unit="个"
          hint="工艺范围"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="FMEA 匹配" subtitle="四级降级：精确 → 规范化 → 语义 → LLM 候选 → 无匹配">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
              {PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  onClick={() => {
                    setForm(preset.body)
                    submit(preset.body)
                  }}
                  className={BTN_GHOST}
                >
                  {preset.label}
                </button>
              ))}
            </div>

            <div className="grid gap-2 sm:grid-cols-3">
              <label className="block sm:col-span-3">
                <span className="mb-1 block text-[11px] text-ink-muted">失效模式</span>
                <input
                  list="failure-modes"
                  value={form.failureMode ?? ''}
                  onChange={(event) =>
                    setForm((previous) => ({ ...previous, failureMode: event.target.value }))
                  }
                  placeholder="例如：一次合格率下降"
                  className={FIELD}
                />
                <datalist id="failure-modes">
                  {failureModes.map((mode) => (
                    <option key={mode} value={mode} />
                  ))}
                </datalist>
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-ink-muted">工艺（可选）</span>
                <input
                  value={form.process ?? ''}
                  onChange={(event) =>
                    setForm((previous) => ({ ...previous, process: event.target.value }))
                  }
                  placeholder="关键扭矩拧紧"
                  className={FIELD}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11px] text-ink-muted">工序（可选）</span>
                <input
                  value={form.operation ?? ''}
                  onChange={(event) =>
                    setForm((previous) => ({ ...previous, operation: event.target.value }))
                  }
                  placeholder="拧紧"
                  className={FIELD}
                />
              </label>
              <button
                type="button"
                onClick={() => submit(form)}
                disabled={!form.failureMode?.trim()}
                className={`${BTN_PRIMARY} sm:self-end`}
              >
                匹配
              </button>
            </div>

            {matchState.loading && (
              <p className="py-4 text-center text-[12px] text-ink-faint">匹配中…</p>
            )}
            {!matchState.loading && matchState.error && (
              <p className="py-4 text-center text-[12px] text-crit">
                匹配失败：{matchState.error}
              </p>
            )}

            {!matchState.loading && match && (
              <div className="space-y-2.5">
                <div className="flex flex-wrap items-center gap-2 rounded border border-panel-line bg-panel-muted px-3 py-2">
                  <span className="text-[10px] text-ink-muted">匹配结果</span>
                  <span
                    className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                      MATCH_CHIP[match.matchStatus]
                    }`}
                  >
                    {MATCH_LABEL[match.matchStatus]}
                  </span>
                  <span className="text-[10px] text-ink-faint">
                    {match.candidates.length} 个候选
                  </span>
                  {match.requiresHumanReview && (
                    <span className="rounded bg-warn-soft px-1.5 py-0.5 text-[10px] font-medium text-warn">
                      需人工复核
                    </span>
                  )}
                </div>

                {match.matchStatus === 'NO_MATCH' && (
                  <p className="rounded border border-crit/30 bg-crit-soft px-3 py-2 text-[11px] leading-relaxed text-crit">
                    未在 Master FMEA 中找到匹配记录。引擎不会编造历史记录，请人工确认根因并补充或更新 FMEA。
                  </p>
                )}

                {match.candidates.map((candidate) => {
                  const key = `${candidate.record.process}:${candidate.record.failureMode}:${candidate.record.cause}`
                  const isConfirmed = confirmedKey === key
                  return (
                    <div
                      key={key}
                      className={`rounded border px-3 py-2 ${
                        isConfirmed ? 'border-ok/40 bg-ok-soft' : 'border-panel-line bg-panel-muted'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-[12px] font-medium text-ink">
                            {candidate.record.cause}
                          </div>
                          <div className="mt-0.5 text-[10px] text-ink-faint">
                            {candidate.record.process} · {candidate.record.operation} ·{' '}
                            {candidate.record.failureMode}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-1.5">
                          <span className="tabular text-[11px] text-ink-muted">
                            相似度 {candidate.score.toFixed(2)}
                          </span>
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                              AP_CHIP[candidate.record.ap]
                            }`}
                          >
                            AP {candidate.record.ap}
                          </span>
                        </div>
                      </div>

                      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-ink-muted">
                        <span className="tabular">
                          S{candidate.record.severity} / O{candidate.record.occurrence} / D
                          {candidate.record.detection}
                        </span>
                        <span className="tabular">RPN {candidate.record.rpn}</span>
                        {candidate.record.category && (
                          <span>{CATEGORY_LABEL[candidate.record.category]}</span>
                        )}
                        <span>影响：{candidate.record.effect}</span>
                      </div>

                      {candidate.record.recommendedAction && (
                        <div className="mt-1 text-[10px] text-ink-faint">
                          既定措施：{candidate.record.recommendedAction}
                        </div>
                      )}

                      {match.requiresHumanReview && (
                        <div className="mt-2 flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setConfirmedKey(isConfirmed ? null : key)}
                            className={isConfirmed ? BTN_GHOST : BTN_PRIMARY}
                          >
                            {isConfirmed ? '取消确认' : '人工确认此条'}
                          </button>
                          {isConfirmed && (
                            <span className="text-[10px] text-ok">
                              已人工确认（演示会话内，未落库）
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {!match && !matchState.loading && (
              <p className="py-2 text-center text-[11px] text-ink-faint">
                输入失效模式，或点击上方预设走一遍四级降级。
              </p>
            )}
          </div>
        </Panel>

        <Panel title="风险矩阵" subtitle="严重度 S × 发生度 O · 单元格取该格最差 AP">
          <div className="overflow-x-auto">
            <table className="w-full border-separate border-spacing-0.5 text-[10px]">
              <thead>
                <tr>
                  <th className="w-6" />
                  {SCORES.map((occurrence) => (
                    <th
                      key={occurrence}
                      className="pb-1 text-center font-medium text-ink-muted"
                    >
                      {occurrence}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...SCORES].reverse().map((severity) => (
                  <tr key={severity}>
                    <td className="pr-1 text-right font-medium text-ink-muted">{severity}</td>
                    {SCORES.map((occurrence) => {
                      const cell = matrix.get(`${severity}:${occurrence}`)
                      return (
                        <td key={occurrence} className="p-0">
                          <div
                            className={`grid h-6 place-items-center rounded ${
                              cell ? AP_CELL[cell.ap] : 'bg-panel-muted text-ink-faint'
                            }`}
                          >
                            {cell ? cell.count : ''}
                          </div>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 text-[10px] text-ink-muted">
              {(['H', 'M', 'L'] as ActionPriority[]).map((ap) => (
                <span key={ap} className="flex items-center gap-1.5">
                  <span className={`h-2.5 w-2.5 rounded-sm ${AP_CELL[ap]}`} />
                  AP {ap}（{AP_LABEL[ap]}）
                </span>
              ))}
            </div>
            <span className="text-[10px] text-ink-faint">S ↑ / O →</span>
          </div>
        </Panel>
      </div>

      <Panel title="Master FMEA 记录" subtitle="RPN / AP 由 S·O·D 确定性派生 · 按 RPN 降序">
        {recordState.loading && (
          <p className="py-8 text-center text-[12px] text-ink-faint">加载中…</p>
        )}
        {!recordState.loading && recordState.error && (
          <p className="py-8 text-center text-[12px] text-crit">
            加载失败：{recordState.error}
          </p>
        )}

        {!recordState.loading && !recordState.error && sortedRecords.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-b border-panel-line text-left text-[11px] text-ink-muted">
                  <th className="py-2 pr-3 font-medium">工艺 / 工序</th>
                  <th className="py-2 pr-3 font-medium">失效模式</th>
                  <th className="py-2 pr-3 font-medium">原因</th>
                  <th className="py-2 pr-2 text-center font-medium">4M1E</th>
                  <th className="py-2 pr-2 text-right font-medium">S</th>
                  <th className="py-2 pr-2 text-right font-medium">O</th>
                  <th className="py-2 pr-2 text-right font-medium">D</th>
                  <th className="py-2 pr-3 text-right font-medium">RPN</th>
                  <th className="py-2 font-medium">AP</th>
                </tr>
              </thead>
              <tbody>
                {sortedRecords.map((record) => (
                  <tr
                    key={`${record.process}:${record.failureMode}:${record.cause}`}
                    className="border-b border-panel-line/70 align-top"
                  >
                    <td className="py-2 pr-3 text-ink-muted">
                      <div>{record.process}</div>
                      <div className="text-[10px] text-ink-faint">{record.operation}</div>
                    </td>
                    <td className="py-2 pr-3 text-ink-muted">{record.failureMode}</td>
                    <td className="py-2 pr-3">
                      <div className="text-ink">{record.cause}</div>
                      {record.recommendedAction && (
                        <div className="mt-0.5 text-[10px] text-ink-faint">
                          {record.recommendedAction}
                        </div>
                      )}
                    </td>
                    <td className="py-2 pr-2 text-center text-ink-muted">
                      {record.category ? CATEGORY_LABEL[record.category] : '—'}
                    </td>
                    <td className="tabular py-2 pr-2 text-right text-ink-muted">
                      {record.severity}
                    </td>
                    <td className="tabular py-2 pr-2 text-right text-ink-muted">
                      {record.occurrence}
                    </td>
                    <td className="tabular py-2 pr-2 text-right text-ink-muted">
                      {record.detection}
                    </td>
                    <td className="tabular py-2 pr-3 text-right font-semibold text-ink">
                      {record.rpn}
                    </td>
                    <td className="py-2">
                      <span
                        className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium ${
                          AP_CHIP[record.ap]
                        }`}
                      >
                        {record.ap}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="mt-3 text-[10px] leading-relaxed text-ink-faint">
          RPN = S × O × D，AP 由 S 与 RPN 阈值判定，二者均由引擎确定性计算，AI 不参与打分。
        </p>
      </Panel>
    </div>
  )
}