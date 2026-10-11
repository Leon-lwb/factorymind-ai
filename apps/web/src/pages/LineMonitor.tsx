import type { MetricKey } from '@factorymind/shared-types'
import { useMemo, useState } from 'react'
import { endpoints } from '../api/endpoints'
import { EChart } from '../components/EChart'
import { Panel } from '../components/Panel'
import { useAsync } from '../hooks/useAsync'
import { buildLineOption, CHART_COLORS } from '../lib/chartTheme'
import { formatClock, formatMetricValue, hoursAgo, metricLabel, toIsoSeconds } from '../lib/format'
import { pivotByStation } from '../lib/metrics'
import { useAppStore } from '../store/useAppStore'

const WINDOW_HOURS = 8

const METRIC_TABS: MetricKey[] = [
  'fpy',
  'nokRate',
  'torqueNokRate',
  'cycleTime',
  'toolErrorRate',
]

const UNIT_BY_METRIC: Record<MetricKey, string> = {
  fpy: '%',
  nokRate: '%',
  torqueNokRate: '%',
  cycleTime: 's',
  toolErrorRate: '%',
}

const EQUIPMENT_TYPE_LABELS: Record<string, string> = {
  TORQUE_TOOL: '拧紧工具',
  PLC: 'PLC',
  SENSOR: '传感器',
  ROBOT: '机器人',
  CONVEYOR: '输送线',
}

export function LineMonitor() {
  const [metric, setMetric] = useState<MetricKey>('fpy')

  const lineId = useAppStore((state) => state.lineId)
  const stationId = useAppStore((state) => state.stationId)
  const stations = useAppStore((state) => state.stations)
  const chooseStation = useAppStore((state) => state.chooseStation)

  const stationNames = useMemo(
    () => Object.fromEntries(stations.map((station) => [station.stationId, station.name])),
    [stations],
  )

  const timeWindow = useMemo(() => {
    const to = new Date()
    to.setSeconds(0, 0)
    return { from: toIsoSeconds(hoursAgo(WINDOW_HOURS)), to: toIsoSeconds(to) }
  }, [])

  const { data: points, loading, error } = useAsync(async () => {
    if (!lineId) return []
    return endpoints.metrics({ metric, ...timeWindow, lineId })
  }, [lineId, metric, timeWindow.from, timeWindow.to])

  const { data: equipment } = useAsync(async () => {
    if (!stationId) return []
    return endpoints.equipment(stationId)
  }, [stationId])

  const { data: processes } = useAsync(async () => {
    if (!stationId) return []
    return endpoints.processes(stationId)
  }, [stationId])

  const pivot = useMemo(() => pivotByStation(points ?? [], stationNames), [points, stationNames])

  const chartOption = useMemo(
    () =>
      buildLineOption({
        categories: pivot.categories,
        series: pivot.series.map((item) => ({
          name: item.name,
          data: item.data,
          // The selected station carries the focal colour; peers stay neutral.
          color:
            item.stationId === stationId ? CHART_COLORS.accent : CHART_COLORS.inkFaint,
        })),
        unit: UNIT_BY_METRIC[metric],
      }),
    [pivot, stationId, metric],
  )

  const selectedStation = stations.find((station) => station.stationId === stationId)

  return (
    <div className="space-y-4">
      <Panel
        title="指标趋势（按工位）"
        subtitle={
          pivot.categories.length > 0
            ? `近 ${WINDOW_HOURS} 小时 · 5 分钟粒度 · 高亮为当前工位`
            : '暂无数据'
        }
        actions={
          <div className="flex flex-wrap gap-1">
            {METRIC_TABS.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setMetric(item)}
                className={[
                  'rounded px-2 py-1 text-[11px] transition-colors',
                  item === metric
                    ? 'bg-accent-soft text-accent-strong'
                    : 'text-ink-muted hover:bg-panel-muted',
                ].join(' ')}
              >
                {metricLabel(item)}
              </button>
            ))}
          </div>
        }
      >
        {loading && <p className="py-8 text-center text-[12px] text-ink-faint">加载中…</p>}
        {!loading && error && (
          <p className="py-8 text-center text-[12px] text-crit">加载失败：{error}</p>
        )}
        {!loading && !error && pivot.series.length > 0 && (
          <EChart option={chartOption} height={280} />
        )}
        {!loading && !error && pivot.series.length === 0 && (
          <p className="py-8 text-center text-[12px] text-ink-faint">该产线暂无工位数据</p>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        <Panel title="工位列表" subtitle={`点击切换当前工位 · 共 ${stations.length} 个`}>
          <ul className="space-y-1">
            {stations.map((station) => (
              <li key={station.stationId}>
                <button
                  type="button"
                  onClick={() => chooseStation(station.stationId)}
                  className={[
                    'flex w-full items-center justify-between rounded border px-3 py-2 text-left text-[12px] transition-colors',
                    station.stationId === stationId
                      ? 'border-accent/40 bg-accent-soft'
                      : 'border-panel-line hover:bg-panel-muted',
                  ].join(' ')}
                >
                  <span className="font-medium text-ink">{station.name}</span>
                  <span className="font-mono text-[11px] text-ink-faint">
                    {station.stationId}
                  </span>
                </button>
              </li>
            ))}
            {stations.length === 0 && (
              <li className="py-6 text-center text-[12px] text-ink-faint">该产线暂无工位</li>
            )}
          </ul>
        </Panel>

        <Panel
          title={selectedStation ? `工位详情 · ${selectedStation.name}` : '工位详情'}
          subtitle="设备与工艺"
        >
          {!selectedStation && (
            <p className="py-6 text-center text-[12px] text-ink-faint">请选择一个工位</p>
          )}
          {selectedStation && (
            <div className="space-y-4">
              <div>
                <h3 className="mb-2 text-[11px] font-medium text-ink-muted">设备</h3>
                {(equipment ?? []).length === 0 ? (
                  <p className="text-[12px] text-ink-faint">该工位未登记设备</p>
                ) : (
                  <ul className="space-y-1.5">
                    {(equipment ?? []).map((item) => (
                      <li
                        key={item.equipmentId}
                        className="flex items-center justify-between rounded border border-panel-line px-3 py-2 text-[12px]"
                      >
                        <span className="text-ink">{item.name}</span>
                        <span className="flex items-center gap-2">
                          <span className="rounded bg-panel-muted px-1.5 py-0.5 text-[10px] text-ink-muted">
                            {EQUIPMENT_TYPE_LABELS[item.type] ?? item.type}
                          </span>
                          <span className="font-mono text-[11px] text-ink-faint">
                            {item.equipmentId}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3 className="mb-2 text-[11px] font-medium text-ink-muted">工艺</h3>
                {(processes ?? []).length === 0 ? (
                  <p className="text-[12px] text-ink-faint">该工位未登记工艺</p>
                ) : (
                  <ul className="space-y-1.5">
                    {(processes ?? []).map((item) => (
                      <li
                        key={item.processId}
                        className="flex items-center justify-between rounded border border-panel-line px-3 py-2 text-[12px]"
                      >
                        <span className="text-ink">{item.name}</span>
                        <span className="font-mono text-[11px] text-ink-faint">
                          {item.operation}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </Panel>
      </div>

      {pivot.categories.length > 0 && (
        <p className="text-[11px] text-ink-faint">
          数据窗口 {formatClock(pivot.categories[0])} —{' '}
          {formatClock(pivot.categories[pivot.categories.length - 1])}
          {selectedStation &&
            ` · 当前工位最新${metricLabel(metric)} ${
              pivot.series
                .find((item) => item.stationId === selectedStation.stationId)
                ?.data.filter((value) => Number.isFinite(value))
                .slice(-1)
                .map((value) => formatMetricValue(value, UNIT_BY_METRIC[metric]))[0] ?? '—'
            }${UNIT_BY_METRIC[metric]}`}
        </p>
      )}
    </div>
  )
}