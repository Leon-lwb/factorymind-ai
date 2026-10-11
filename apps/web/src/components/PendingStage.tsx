import { Panel } from './Panel'

export interface PendingStageProps {
  stage: string
  planned: string[]
}

/**
 * Placeholder for a page whose engine is not built yet. It states which stage
 * delivers the page and what it will contain, so the scaffold never shows an
 * empty or misleading screen.
 */
export function PendingStage({ stage, planned }: PendingStageProps) {
  return (
    <Panel title="本页待实现" subtitle={`计划在 ${stage} 阶段交付`}>
      <ul className="space-y-1.5 text-[12px] text-ink-muted">
        {planned.map((item) => (
          <li key={item} className="flex gap-2">
            <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-accent" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </Panel>
  )
}