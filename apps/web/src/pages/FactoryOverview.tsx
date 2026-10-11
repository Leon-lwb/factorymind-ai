import type { MetricPoint } from '@factorymind/shared-types'
import { useMemo } from 'react'
import { endpoints } from '../api/endpoints'
import { EChart } from '../components/EChart'
import { KpiCard, type KpiTone } from '../components/KpiCard'
import { Panel } from '../components/Panel'
import { useAsync } from '../hooks/useAsync'
import { buildLineOption } from '../lib/chartTheme'
import { formatClock, formatMetricValue, hoursAgo, toIsoSeconds } from '../lib/format'
import { latestByStation, mean, pivotByStation } from '../lib/metrics'
import { useAppStore } from '../store/useAppStore'

const WINDOW_HOURS = 8

const EMPTY: MetricPoint[] = []

export function FactoryOverview() {
  const lineId = useAppStore((state) => state.lineId)
  const lineName = useAppStore(
    (state) => state.lines.find((item) => item.lineId === state.lineId)?.name,
  )
  const stations = useAppStore((state) => state.stations)

  const stationNames = useMemo(
    () => Object.fromEntries(stations.map((station) => [station.stationId, station.name])),
    [stations],
  )

  const timeWindow = useMemo(() => {
    const to = new Date()
    to.setSeconds(0, 0)
    return { from: toIsoSeconds(hoursAgo(WINDOW_HOURS)), to: toIsoSeconds(to) }
  }, [])

  const { data, loading, error } = useAsync(async () => {
    if (!lineId) {
      return { fpy: EMPTY, nok: EMPTY, cycle: EMPTY, tool: EMPTY }
    }
    const scope = { ...timeWindow, lineId }
    const [fpy, nok, cycle, tool] = await Promise.all([
      endpoints.metrics({ metric: 'fpy', ...scope }),
      endpoints.metrics({ metric: 'nokRate', ...scope }),
      endpoints.metrics({ metric: 'cycleTime', ...scope }),
      endpoints.metrics({ metric: 'toolErrorRate', ...scope }),
    ])
    return { fpy, nok, cycle, tool }
  }, [lineId, timeWindow.from, timeWindow.to])

  const fpyPoints = data?.fpy ?? EMPTY
  const pivot = useMemo(
    () => pivotByStation(fpyPoints, stationNames),
    [fpyPoints, stationNames],
  )
  const latestFpy = useMemo(() => latestByStation(fpyPoints), [fpyPoints])
  const latestCycle = useMemo(() => latestByStation(data?.cycle ?? EMPTY), [data?.cycle])
  const latestTool = useMemo(() => latestByStation(data?.tool ?? EMPTY), [data?.tool])
  const latestNok = useMemo(() => latestByStation(data?.nok ?? EMPTY), [data?.nok])

  const fpyAverage = mean([...latestFpy.values()].map((point) => point.value))
  const nokAverage = mean([...latestNok.values()].map((point) => point.value))
  const cycleAverage = mean([...latestCycle.values()].map((point) => point.value))
  const toolAverage = mean([...latestTool.values()].map((point) => point.value))

  const chartOption = useMemo(
    () =>
      buildLineOption({
        categories: pivot.categories,
        series: pivot.series.map((item) => ({ name: item.name, data: item.data })),
        unit: '%',
        yAxisName: '%',
      }),
    [pivot],
  )

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="一次合格率 FPY"
          value={fpyAverage === null ? '—' : fpyAverage.toFixed(2)}
          unit="%"
          hint={`${lineName ?? '—'} · 近 ${WINDOW_HOURS} 小时`}
          tone={higherIsBetter(fpyAverage, 99.5, 99.0)}
        />
        <KpiCard
          label="不良率 NOK"
          value={nokAverage === null ? '—' : nokAverage.toFixed(2)}
          unit="%"
          hint="全部工位均值"
          tone={lowerIsBetter(nokAverage, 0.5, 1.0)}
        />
        <KpiCard
          label="平均节拍"
          value={cycleAverage === null ? '—' : cycleAverage.toFixed(1)}
          unit="s"
          hint="全部工位均值"
        />
        <KpiCard
          label="工具故障率"
          value={toolAverage === null ? '—' : toolAverage.toFixed(2)}
          unit="%"
          hint="拧紧工具相关"
          tone={lowerIsBetter(toolAverage, 0.3, 0.6)}
        />
      </div>

      <Panel
        title="一次合格率趋势（按工位）"
        subtitle={
          pivot.categories.length > 0
            ? `${formatClock(pivot.categories[0])} — ${formatClock(
                pivot.categories[pivot.categories.length - 1],
              )} · 5 分钟粒度`
            : '暂无数据'
        }
      >
        {loading && <p className="py-8 text-center text-[12px] text-ink-faint">加载中…</p>}
        {!loading && error && (
          <p className="py-8 text-center text-[12px] text-crit">加载失败：{error}</p>
        )}
        {!loading && !error && pivot.series.length > 0 && (
          <EChart option={chartOption} height={260} />
        )}
        {!loading && !error && pivot.series.length === 0 && (
          <p className="py-8 text-center text-[12px] text-ink-faint">该产线暂无工位数据</p>
        )}
      </Panel>

      <Panel title="工位状态" subtitle={`共 ${stations.length} 个工位`}>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-panel-line text-left text-[11px] text-ink-muted">
                <th className="py-2 pr-3 font-medium">工位</th>
                <th className="py-2 pr-3 font-medium">工位编号</th>
                <th className="py-2 pr-3 text-right font-medium">一次合格率</th>
                <th className="py-2 pr-3 text-right font-medium">不良率</th>
                <th className="py-2 pr-3 text-right font-medium">节拍</th>
                <th className="py-2 text-right font-medium">工具故障率</th>
              </tr>
            </thead>
            <tbody>
              {stations.map((station) => {
                const fpy = latestFpy.get(station.stationId)
                const nok = latestNok.get(station.stationId)
                const cycle = latestCycle.get(station.stationId)
                const tool = latestTool.get(station.stationId)
                return (
                  <tr
                    key={station.stationId}
                    className="border-b border-panel-line/70 last:border-0"
                  >
                    <td className="py-2 pr-3 font-medium text-ink">{station.name}</td>
                    <td className="py-2 pr-3 font-mono text-[11px] text-ink-faint">
                      {station.stationId}
                    </td>
                    <td className="tabular py-2 pr-3 text-right">
                      {fpy ? `${formatMetricValue(fpy.value, '%')}%` : '—'}
                    </td>
                    <td className="tabular py-2 pr-3 text-right">
                      {nok ? `${formatMetricValue(nok.value, '%')}%` : '—'}
                    </td>
                    <td className="tabular py-2 pr-3 text-right">
                      {cycle ? `${formatMetricValue(cycle.value, 's')} s` : '—'}
                    </td>
                    <td className="tabular py-2 text-right">
                      {tool ? `${formatMetricValue(tool.value, '%')}%` : '—'}
                    </td>
                  </tr>
                )
              })}
              {stations.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-ink-faint">
                    该产线暂无工位
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

function higherIsBetter(value: number | null, ok: number, warn: number): KpiTone {
  if (value === null) return 'neutral'
  if (value >= ok) return 'ok'
  if (value >= warn) return 'warn'
  return 'crit'
}

function lowerIsBetter(value: number | null, ok: number, warn: number): KpiTone {
  if (value === null) return 'neutral'
  if (value <= ok) return 'ok'
  if (value <= warn) return 'warn'
  return 'crit'
}