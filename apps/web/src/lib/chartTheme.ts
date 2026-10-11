import type { EChartsCoreOption } from 'echarts/core'

/** Palette mirrors the design tokens in `index.css` (spec §17). */
export const CHART_COLORS = {
  ink: '#0F1A22',
  inkMuted: '#5A6B78',
  inkFaint: '#8A99A5',
  panelLine: '#E1E8ED',
  accent: '#0FA697',
  warn: '#D9911F',
  crit: '#CF4238',
  ok: '#2B9663',
} as const

/** Peer series colours — one hue per station, brand-first then neutrals. */
export const SERIES_COLORS = [
  CHART_COLORS.accent,
  '#4C7FB8',
  '#8A6FB0',
  '#B08A4C',
  '#5F8C7A',
] as const

export function axisLabelFormatter(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

interface LineChartOptions {
  categories: string[]
  series: { name: string; data: number[]; color?: string }[]
  unit: string
  yAxisName?: string
}

export function buildLineOption({
  categories,
  series,
  unit,
  yAxisName,
}: LineChartOptions): EChartsCoreOption {
  return {
    color: series.map((item, index) => item.color ?? SERIES_COLORS[index % SERIES_COLORS.length]),
    animationDuration: 180,
    grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#FFFFFF',
      borderColor: CHART_COLORS.panelLine,
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: CHART_COLORS.ink, fontSize: 12 },
      valueFormatter: (value: unknown) =>
        typeof value === 'number' ? `${value.toFixed(2)} ${unit}` : String(value),
    },
    legend: {
      show: series.length > 1,
      top: 0,
      right: 0,
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: CHART_COLORS.inkMuted, fontSize: 11 },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: categories,
      axisLine: { lineStyle: { color: CHART_COLORS.panelLine } },
      axisTick: { show: false },
      axisLabel: {
        color: CHART_COLORS.inkFaint,
        fontSize: 11,
        formatter: axisLabelFormatter,
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value',
      name: yAxisName,
      scale: true,
      nameTextStyle: { color: CHART_COLORS.inkFaint, fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: CHART_COLORS.panelLine, type: 'dashed' } },
      axisLabel: { color: CHART_COLORS.inkFaint, fontSize: 11 },
    },
    series: series.map((item) => ({
      name: item.name,
      type: 'line',
      smooth: false,
      showSymbol: false,
      lineStyle: { width: 1.6 },
      data: item.data,
    })),
  }
}