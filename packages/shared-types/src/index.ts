/**
 * FactoryMind AI — shared domain contract.
 *
 * Single source of truth for the TypeScript side. The Python services mirror
 * these shapes via Pydantic models; the JSON wire format must stay identical
 * so the Mock and supOS providers are interchangeable.
 *
 * Spec references: §9 data model, §15 AI output, §22 providers, §26 core types.
 */

/* ------------------------------------------------------------------ *
 * Industrial hierarchy (spec §9)
 * ------------------------------------------------------------------ */

export interface Factory {
  factoryId: string;
  name: string;
  country: string;
}

export interface Line {
  lineId: string;
  factoryId: string;
  name: string;
}

export interface Station {
  stationId: string;
  lineId: string;
  name: string;
}

export type EquipmentType =
  | 'TORQUE_TOOL'
  | 'PLC'
  | 'SENSOR'
  | 'ROBOT'
  | 'CONVEYOR';

export interface Equipment {
  equipmentId: string;
  stationId: string;
  type: EquipmentType;
  name: string;
}

export interface Process {
  processId: string;
  stationId: string;
  name: string;
  operation: string;
}

/* ------------------------------------------------------------------ *
 * Quality records and metrics (spec §9.6, §13)
 * ------------------------------------------------------------------ */

export type QualityResult = 'OK' | 'NOK';

export interface QualityRecord {
  timestamp: string;
  stationId: string;
  equipmentId: string;
  result: QualityResult;
  torque: number;
  targetTorque: number;
  cycleTime: number;
}

export type MetricKey =
  | 'fpy'
  | 'nokRate'
  | 'torqueNokRate'
  | 'cycleTime'
  | 'toolErrorRate';

export interface MetricPoint {
  timestamp: string;
  stationId: string;
  metric: MetricKey;
  value: number;
  unit: string;
}

export interface MetricQuery {
  metric: MetricKey;
  from: string;
  to: string;
  stationId?: string;
  lineId?: string;
}

/* ------------------------------------------------------------------ *
 * Industrial events — the temporal anchors for evidence correlation (spec §10.3)
 * ------------------------------------------------------------------ */

export type IndustrialEventType =
  | 'MAINTENANCE'
  | 'TOOL_ERROR'
  | 'PLC_ERROR'
  | 'SENSOR_FAULT'
  | 'MATERIAL_CHANGE'
  | 'SHIFT_CHANGE';

export interface IndustrialEvent {
  eventId: string;
  timestamp: string;
  stationId: string;
  equipmentId?: string;
  type: IndustrialEventType;
  severity: RiskLevel;
  message: string;
}

export interface EventQuery {
  from: string;
  to: string;
  stationId?: string;
  lineId?: string;
  types?: IndustrialEventType[];
}

/* ------------------------------------------------------------------ *
 * Anomaly (spec §13, §26)
 * ------------------------------------------------------------------ */

export type AnomalyStatus =
  | 'NEW'
  | 'ANALYZING'
  | 'CONFIRMED'
  | 'ACTION_CREATED'
  | 'RESOLVED';

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH';

/** Which way the metric moved. Only the bad direction is ever reported. */
export type AnomalyDirection = 'UP' | 'DOWN';

export interface Anomaly {
  id: string;
  timestamp: string;
  factoryId: string;
  lineId: string;
  stationId: string;
  metric: MetricKey;
  /** Robust baseline (median) of the detection window. */
  baseline: number;
  /** Peak excursion of the episode — the worst reading an operator would see. */
  actual: number;
  delta: number;
  /** `delta / baseline`, e.g. -0.022 for a 2.2% drop. */
  deltaRatio: number;
  direction: AnomalyDirection;
  /** Peak excursion expressed in robust sigmas (MAD-derived). */
  zScore: number;
  /** 0-1 anomaly score. Deterministic output of the anomaly engine. */
  severity: number;
  status: AnomalyStatus;
  /** Span of the merged episode, not of the query window. */
  windowStart: string;
  windowEnd: string;
  evidence: Evidence[];
}

export interface AnomalyQuery {
  from: string;
  to: string;
  stationId?: string;
  lineId?: string;
}

/* ------------------------------------------------------------------ *
 * Evidence (spec §7, §14, §26)
 * ------------------------------------------------------------------ */

export type EvidenceType =
  | 'METRIC_SHIFT'
  | 'EVENT_CORRELATION'
  | 'HISTORICAL_PATTERN'
  | 'PROCESS_CONTEXT';

export interface Evidence {
  id: string;
  type: EvidenceType;
  source: string;
  value: string | number;
  /** 0-1 relevance used by the deterministic evidence scorer. */
  relevance: number;
  timestamp?: string;
}

/* ------------------------------------------------------------------ *
 * 4M1E (spec §11)
 * ------------------------------------------------------------------ */

export type FourMOneECategory =
  | 'MAN'
  | 'MACHINE'
  | 'MATERIAL'
  | 'METHOD'
  | 'ENVIRONMENT';

/* ------------------------------------------------------------------ *
 * Root cause (spec §14, §15, §26)
 * ------------------------------------------------------------------ */

export interface RootCauseCandidate {
  cause: string;
  /** 0-1. Never presented as certainty — see spec §7. */
  confidence: number;
  evidenceIds: string[];
  category?: FourMOneECategory;
}

/** Configurable weights for the deterministic evidence score (spec §14). */
export interface RootCauseWeights {
  correlation: number;
  temporal: number;
  magnitude: number;
  fmeaRelevance: number;
  historicalEvidence: number;
}

/* ------------------------------------------------------------------ *
 * FMEA (spec §8, §12, §26)
 * ------------------------------------------------------------------ */

export type ActionPriority = 'H' | 'M' | 'L';

export interface FmeaRecord {
  process: string;
  operation: string;
  failureMode: string;
  effect: string;
  cause: string;
  preventionControl?: string;
  detectionControl?: string;
  severity: number;
  occurrence: number;
  detection: number;
  rpn: number;
  ap: ActionPriority;
  recommendedAction?: string;
  /** Which 4M1E axis the cause belongs to (spec §11). */
  category?: FourMOneECategory;
}

/**
 * Match precedence is fixed by spec §8.1.
 * `NO_MATCH` must never be papered over with an invented record (spec §8.2).
 */
export type FmeaMatchStatus =
  | 'EXACT'
  | 'NORMALIZED'
  | 'SEMANTIC'
  | 'LLM_CANDIDATE'
  | 'NO_MATCH';

export interface FmeaCandidate {
  record: FmeaRecord;
  /** 0-1 match score. */
  score: number;
  matchType: FmeaMatchStatus;
}

export interface FmeaMatchResult {
  matchStatus: FmeaMatchStatus;
  requiresHumanReview: boolean;
  candidates: FmeaCandidate[];
}

/* ------------------------------------------------------------------ *
 * Risk assessment (spec §12, §15)
 * ------------------------------------------------------------------ */

export interface RiskAssessment {
  severity: number;
  occurrence: number;
  detection: number;
  rpn: number;
  ap: ActionPriority;
}

/* ------------------------------------------------------------------ *
 * AI analysis (spec §15)
 * ------------------------------------------------------------------ */

export interface RecommendedAction {
  action: string;
  priority: RiskLevel;
  rationale?: string;
  /**
   * Evidence ids this recommendation is grounded in. Validated server-side: a
   * citation to evidence that does not exist is rejected, not displayed.
   */
  evidenceIds: string[];
}

export interface AiAnalysisInput {
  anomaly: Anomaly;
  evidence: Evidence[];
  process?: Process;
  equipment?: Equipment;
  fmeaCandidates: FmeaCandidate[];
}

export interface AiAnalysisResult {
  summary: string;
  rootCauseCandidates: RootCauseCandidate[];
  risk: RiskAssessment;
  recommendedActions: RecommendedAction[];
  /** Provider that produced this result, e.g. `mock` or `openai-compatible`. */
  provider: string;
  generatedAt: string;
}

/* ------------------------------------------------------------------ *
 * Analysis surface (spec §15, §25)
 * ------------------------------------------------------------------ */

export interface AnalysisRequest {
  /** The anomaly as returned by /api/anomalies, evidence included. */
  anomaly: Anomaly;
  processId?: string;
}

export interface AnalysisResponse {
  anomaly: Anomaly;
  fmeaMatch: FmeaMatchResult;
  analysis: AiAnalysisResult;
}

export interface FmeaMatchRequest {
  failureMode: string;
  process?: string;
  operation?: string;
}

/* ------------------------------------------------------------------ *
 * Action loop and verification (spec §16 Page 6, §46)
 * ------------------------------------------------------------------ */

export type ActionItemStatus = 'OPEN' | 'IN_PROGRESS' | 'DONE';

export interface ActionItem {
  id: string;
  anomalyId: string;
  title: string;
  owner: string;
  dueDate: string;
  status: ActionItemStatus;
  priority: RiskLevel;
}

export interface Verification {
  actionId: string;
  verifiedAt: string;
  metric: MetricKey;
  before: number;
  after: number;
  target: number;
  passed: boolean;
}

/**
 * The client echoes the anomaly it is acting on. The engine keeps that snapshot,
 * so verification re-measures the same station and metric without trusting a
 * client-supplied reading.
 */
export interface ActionCreateRequest {
  anomaly: Anomaly;
  title: string;
  owner: string;
  dueDate: string;
  priority: RiskLevel;
}

export interface ActionUpdateRequest {
  status?: ActionItemStatus;
  owner?: string;
  dueDate?: string;
}

/** Board read model: Page 6 renders from this in one round trip. */
export interface ActionWithContext {
  action: ActionItem;
  anomaly: Anomaly;
  latestVerification?: Verification;
}

export interface VerifyActionResponse {
  action: ActionItem;
  verification: Verification;
}

/* ------------------------------------------------------------------ *
 * Provider contracts (spec §22)
 * ------------------------------------------------------------------ */

export interface IndustrialDataProvider {
  getFactories(): Promise<Factory[]>;
  getLines(factoryId: string): Promise<Line[]>;
  getStations(lineId: string): Promise<Station[]>;
  getMetrics(query: MetricQuery): Promise<MetricPoint[]>;
  getEvents(query: EventQuery): Promise<IndustrialEvent[]>;
}

/* ------------------------------------------------------------------ *
 * Runtime configuration (spec §20, §42)
 * ------------------------------------------------------------------ */

export type AiProviderKind = 'mock' | 'openai-compatible' | 'local';
export type DataProviderKind = 'mock' | 'supos';

export interface RuntimeConfig {
  aiProvider: AiProviderKind;
  dataProvider: DataProviderKind;
  supOsApiUrl?: string;
}