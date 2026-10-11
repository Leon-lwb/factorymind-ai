import type { Factory, Line, Station } from '@factorymind/shared-types'
import { create } from 'zustand'
import { endpoints } from '../api/endpoints'

export type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

interface AppState {
  factories: Factory[]
  lines: Line[]
  stations: Station[]
  factoryId: string | null
  lineId: string | null
  stationId: string | null
  status: LoadStatus
  error: string | null
  dataProvider: string
  init: () => Promise<void>
  chooseFactory: (factoryId: string) => Promise<void>
  chooseLine: (lineId: string) => Promise<void>
  chooseStation: (stationId: string | null) => void
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export const useAppStore = create<AppState>((set) => ({
  factories: [],
  lines: [],
  stations: [],
  factoryId: null,
  lineId: null,
  stationId: null,
  status: 'idle',
  error: null,
  dataProvider: 'unknown',

  init: async () => {
    set({ status: 'loading', error: null })
    try {
      const [health, factories] = await Promise.all([
        endpoints.health(),
        endpoints.factories(),
      ])
      const factoryId = factories[0]?.factoryId ?? null
      const lines = factoryId ? await endpoints.lines(factoryId) : []
      const lineId = lines[0]?.lineId ?? null
      const stations = lineId ? await endpoints.stations(lineId) : []

      set({
        dataProvider: health.dataProvider,
        factories,
        factoryId,
        lines,
        lineId,
        stations,
        stationId: stations[0]?.stationId ?? null,
        status: 'ready',
      })
    } catch (error) {
      set({ status: 'error', error: messageOf(error) })
    }
  },

  chooseFactory: async (factoryId) => {
    set({ status: 'loading', error: null, factoryId, stationId: null })
    try {
      const lines = await endpoints.lines(factoryId)
      const lineId = lines[0]?.lineId ?? null
      const stations = lineId ? await endpoints.stations(lineId) : []
      set({
        lines,
        lineId,
        stations,
        stationId: stations[0]?.stationId ?? null,
        status: 'ready',
      })
    } catch (error) {
      set({ status: 'error', error: messageOf(error) })
    }
  },

  chooseLine: async (lineId) => {
    set({ status: 'loading', error: null, lineId, stationId: null })
    try {
      const stations = await endpoints.stations(lineId)
      set({
        stations,
        stationId: stations[0]?.stationId ?? null,
        status: 'ready',
      })
    } catch (error) {
      set({ status: 'error', error: messageOf(error) })
    }
  },

  chooseStation: (stationId) => set({ stationId }),
}))

export function useCurrentNames(): {
  factory?: Factory
  line?: Line
  station?: Station
} {
  const factories = useAppStore((s) => s.factories)
  const lines = useAppStore((s) => s.lines)
  const stations = useAppStore((s) => s.stations)
  const factoryId = useAppStore((s) => s.factoryId)
  const lineId = useAppStore((s) => s.lineId)
  const stationId = useAppStore((s) => s.stationId)

  return {
    factory: factories.find((item) => item.factoryId === factoryId),
    line: lines.find((item) => item.lineId === lineId),
    station: stations.find((item) => item.stationId === stationId),
  }
}