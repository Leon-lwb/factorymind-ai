import type { MetricPoint } from '@factorymind/shared-types'

export interface PivotedSeries {
  categories: string[]
  series: { stationId: string; name: string; data: number[] }[]
}

/**
 * Turns long-format metric points into one series per station, aligned on a
 * shared time axis so every line starts and ends at the same instant.
 */
export function pivotByStation(
  points: MetricPoint[],
  stationNames: Record<string, string>,
): PivotedSeries {
  const categories = [...new Set(points.map((point) => point.timestamp))].sort()

  const byStation = new Map<string, Map<string, number>>()
  for (const point of points) {
    let bucket = byStation.get(point.stationId)
    if (!bucket) {
      bucket = new Map()
      byStation.set(point.stationId, bucket)
    }
    bucket.set(point.timestamp, point.value)
  }

  const series = [...byStation.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([stationId, bucket]) => ({
      stationId,
      name: stationNames[stationId] ?? stationId,
      data: categories.map((category) => bucket.get(category) ?? Number.NaN),
    }))

  return { categories, series }
}

export function latestByStation(points: MetricPoint[]): Map<string, MetricPoint> {
  const latest = new Map<string, MetricPoint>()
  for (const point of points) {
    const current = latest.get(point.stationId)
    if (!current || point.timestamp > current.timestamp) {
      latest.set(point.stationId, point)
    }
  }
  return latest
}

export function mean(values: number[]): number | null {
  const usable = values.filter((value) => Number.isFinite(value))
  if (usable.length === 0) return null
  return usable.reduce((sum, value) => sum + value, 0) / usable.length
}