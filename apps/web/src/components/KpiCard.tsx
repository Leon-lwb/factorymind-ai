export type KpiTone = 'neutral' | 'ok' | 'warn' | 'crit'

const TONE_CLASS: Record<KpiTone, string> = {
  neutral: 'text-ink',
  ok: 'text-ok',
  warn: 'text-warn',
  crit: 'text-crit',
}

export interface KpiCardProps {
  label: string
  value: string
  unit?: string
  hint?: string
  tone?: KpiTone
}

export function KpiCard({ label, value, unit, hint, tone = 'neutral' }: KpiCardProps) {
  return (
    <div className="rounded-lg border border-panel-line bg-panel px-4 py-3">
      <div className="text-[11px] text-ink-muted">{label}</div>
      <div className="mt-1.5 flex items-baseline gap-1">
        <span className={`tabular text-[24px] font-semibold leading-none ${TONE_CLASS[tone]}`}>
          {value}
        </span>
        {unit && <span className="text-[11px] text-ink-faint">{unit}</span>}
      </div>
      {hint && <div className="mt-1.5 text-[11px] text-ink-faint">{hint}</div>}
    </div>
  )
}