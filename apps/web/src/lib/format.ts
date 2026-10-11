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
 * Formats an instant the way the engine expects it: local wall-clock, no zone
 * suffix. `toISOString()` would emit UTC, which then round-trips back through
 * `new Date(...)` as local time and silently shifts every label by the offset.
 */
export function toIsoSeconds(date: Date): string {
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
  ].join('T')
}

export function hoursAgo(hours: number): Date {
  return new Date(Date.now() - hours * 3600_000)
}