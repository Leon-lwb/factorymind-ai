import type {
  ActionCreateRequest,
  ActionItem,
  ActionItemStatus,
  ActionUpdateRequest,
  ActionWithContext,
  AnalysisRequest,
  AnalysisResponse,
  Anomaly,
  AnomalyQuery,
  Equipment,
  EventQuery,
  Factory,
  FmeaMatchRequest,
  FmeaMatchResult,
  FmeaRecord,
  IndustrialEvent,
  Line,
  MetricPoint,
  MetricQuery,
  Process,
  Station,
  VerifyActionResponse,
} from '@factorymind/shared-types'
import { http } from './client'

/**
 * Typed access to the AI engine API.
 *
 * The mock/supOS data-source switch lives server-side (`DATA_PROVIDER`), so the
 * browser never holds supOS credentials and never needs CORS access to the
 * platform gateway. The `IndustrialDataProvider` contract is therefore
 * implemented in `services/ai-engine`, and this module only types the wire
 * format.
 */

export interface HealthStatus {
  status: string
  dataProvider: string
  aiProvider: string
  version: string
}

export const endpoints = {
  health: () => http.get<HealthStatus>('/health'),

  factories: () => http.get<Factory[]>('/factories'),

  lines: (factoryId: string) => http.get<Line[]>('/lines', { factoryId }),

  stations: (lineId: string) => http.get<Station[]>('/stations', { lineId }),

  equipment: (stationId: string) =>
    http.get<Equipment[]>('/equipment', { stationId }),

  processes: (stationId: string) =>
    http.get<Process[]>('/processes', { stationId }),

  metrics: (query: MetricQuery) =>
    http.get<MetricPoint[]>('/metrics', {
      metric: query.metric,
      from: query.from,
      to: query.to,
      stationId: query.stationId,
      lineId: query.lineId,
    }),

  events: (query: EventQuery) =>
    http.get<IndustrialEvent[]>('/events', {
      from: query.from,
      to: query.to,
      stationId: query.stationId,
      lineId: query.lineId,
      types: query.types?.join(','),
    }),

  anomalies: (query: AnomalyQuery) =>
    http.get<Anomaly[]>('/anomalies', {
      from: query.from,
      to: query.to,
      stationId: query.stationId,
      lineId: query.lineId,
    }),

  analysis: (body: AnalysisRequest) =>
    http.post<AnalysisResponse>('/analysis', body),

  fmeaRecords: () => http.get<FmeaRecord[]>('/fmea/records'),

  fmeaMatch: (body: FmeaMatchRequest) =>
    http.post<FmeaMatchResult>('/fmea/match', body),

  actions: (status?: ActionItemStatus) =>
    http.get<ActionWithContext[]>('/actions', { status }),

  createAction: (body: ActionCreateRequest) =>
    http.post<ActionItem>('/actions', body),

  updateAction: (actionId: string, body: ActionUpdateRequest) =>
    http.patch<ActionItem>(`/actions/${actionId}`, body),

  verifyAction: (actionId: string) =>
    http.post<VerifyActionResponse>(`/actions/${actionId}/verify`, {}),
}