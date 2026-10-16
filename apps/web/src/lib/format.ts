const METRIC_LABELS: Record<string, string> = {
  fpy: '一次合格率 FPY',
  nokRate: '不良率 NOK',
  torqueNokRate: '扭矩不合格率',
  cycleTime: '节拍',
  toolErrorRate: '工具故障率',
}

export function metricLabel(metric: string): string {
  return METRIC_LABELS[metric] ?? metric
}

const METRIC_UNITS: Record<string, string> = {
  fpy: '%',
  nokRate: '%',
  torqueNokRate: '%',
  cycleTime: 's',
  toolErrorRate: '%',
}

export function metricUnit(metric: string): string {
  return METRIC_UNITS[metric] ?? ''
}

export function formatMetricValue(value: number, unit: string): string {
  if (unit === 's') return `${value.toFixed(1)}`
  return `${value.toFixed(2)}`
}

export function formatClock(iso: string): string {
  const date = new Date(iso)
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`
}

export function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/**
 * Formats an instant the way the engine expects it: a bare wall-clock string
 * with no zone suffix. The engine tags naive values as UTC before calling
 * supOS (`_iso()` in the provider), so this must emit **UTC** wall-clock —
 * emitting local wall-clock shifts the query window by the browser's offset
 * (+08:00 here), which makes every history request miss the stored data.
 * Point timestamps come back from the engine with an explicit zone, so display
 * labels stay correct either way.
 */
export function toIsoSeconds(date: Date): string {
  return [
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`,
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`,
  ].join('T')
}

export function hoursAgo(hours: number): Date {
  return new Date(Date.now() - hours * 3600_000)
}