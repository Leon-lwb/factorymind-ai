import { useEffect } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAppStore } from '../store/useAppStore'

interface NavItem {
  to: string
  label: string
  icon: string
}

const NAV_ITEMS: NavItem[] = [
  { to: '/overview', label: '工厂总览', icon: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z' },
  { to: '/line', label: '产线监控', icon: 'M3 12h4l3-7 4 14 3-7h4' },
  { to: '/anomalies', label: '异常中心', icon: 'M12 3l9 16H3zM12 10v4M12 17v.5' },
  { to: '/analysis', label: 'AI 分析', icon: 'M12 3v4M12 17v4M3 12h4M17 12h4M6 6l3 3M15 15l3 3M18 6l-3 3M9 15l-3 3' },
  { to: '/fmea', label: 'FMEA 与风险', icon: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z' },
  { to: '/actions', label: '行动与验证', icon: 'M20 6L9 17l-5-5' },
]

function pageTitle(pathname: string): string {
  const item = NAV_ITEMS.find((entry) => pathname.startsWith(entry.to))
  return item?.label ?? '工厂总览'
}

export function AppShell() {
  const location = useLocation()
  const init = useAppStore((state) => state.init)
  const status = useAppStore((state) => state.status)
  const error = useAppStore((state) => state.error)
  const dataProvider = useAppStore((state) => state.dataProvider)

  const factories = useAppStore((state) => state.factories)
  const lines = useAppStore((state) => state.lines)
  const stations = useAppStore((state) => state.stations)
  const factoryId = useAppStore((state) => state.factoryId)
  const lineId = useAppStore((state) => state.lineId)
  const stationId = useAppStore((state) => state.stationId)
  const chooseFactory = useAppStore((state) => state.chooseFactory)
  const chooseLine = useAppStore((state) => state.chooseLine)
  const chooseStation = useAppStore((state) => state.chooseStation)

  useEffect(() => {
    if (status === 'idle') void init()
  }, [status, init])

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-52 shrink-0 flex-col border-r border-shell-line bg-shell-800">
        <div className="flex h-14 items-center gap-2.5 border-b border-shell-line px-4">
          <span className="grid h-7 w-7 place-items-center rounded bg-accent text-[13px] font-bold text-white">
            F
          </span>
          <div className="leading-tight">
            <div className="text-[13px] font-semibold text-on-shell">FactoryMind AI</div>
            <div className="text-[10px] text-on-shell-muted">质量与风险智能助手</div>
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 p-2">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                [
                  'flex items-center gap-2.5 rounded px-2.5 py-2 text-[13px] transition-colors',
                  isActive
                    ? 'bg-shell-700 text-on-shell'
                    : 'text-on-shell-muted hover:bg-shell-700/60 hover:text-on-shell',
                ].join(' ')
              }
            >
              <svg
                viewBox="0 0 24 24"
                className="h-4 w-4 shrink-0"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d={item.icon} />
              </svg>
              <span className="truncate">{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-shell-line px-3 py-3 text-[10px] leading-relaxed text-on-shell-muted">
          <div className="flex items-center gap-1.5">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                status === 'error' ? 'bg-crit' : 'bg-accent'
              }`}
            />
            数据源：{dataProvider === 'mock' ? '演示数据 (Mock)' : dataProvider}
          </div>
          <div className="mt-1 text-on-shell-muted/70">基于 open supOS</div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-shell-line bg-shell-800 px-5">
          <h1 className="text-[14px] font-semibold text-on-shell">
            {pageTitle(location.pathname)}
          </h1>

          <div className="ml-auto flex items-center gap-2">
            <Selector
              label="工厂"
              value={factoryId ?? ''}
              options={factories.map((item) => ({ value: item.factoryId, label: item.name }))}
              onChange={(value) => void chooseFactory(value)}
            />
            <Selector
              label="产线"
              value={lineId ?? ''}
              options={lines.map((item) => ({ value: item.lineId, label: item.name }))}
              onChange={(value) => void chooseLine(value)}
            />
            <Selector
              label="工位"
              value={stationId ?? ''}
              options={stations.map((item) => ({
                value: item.stationId,
                label: item.name,
              }))}
              onChange={chooseStation}
            />
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-auto bg-panel-muted p-5">
          {status === 'error' && (
            <div className="mb-4 rounded border border-crit/30 bg-crit-soft px-4 py-3 text-[12px] text-crit">
              无法连接引擎：{error}。请确认后端已启动（<code className="font-mono">python -m app</code>）。
            </div>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  )
}

interface SelectorProps {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
}

function Selector({ label, value, options, onChange }: SelectorProps) {
  return (
    <label className="flex items-center gap-1.5 text-[11px] text-on-shell-muted">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={options.length === 0}
        className="rounded border border-shell-600 bg-shell-700 px-2 py-1 text-[12px] text-on-shell outline-none focus:border-accent disabled:opacity-50"
      >
        {options.length === 0 && <option value="">—</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}