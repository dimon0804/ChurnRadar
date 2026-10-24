import { request } from './client'
import type { components } from './schema'

/** Коннекторы-источники (pull): ingest сам ходит в API биллинга и в Яндекс Метрику. Только admin. */
type Schemas = components['schemas']

export type Connector = Schemas['Connector']
export type ConnectorList = Schemas['ConnectorList']
export type ConnectorPatch = Schemas['ConnectorPatch']
export type ConnectorCreate = Schemas['ConnectorCreate']
export type ConnectorRun = Schemas['ConnectorRun']
export type ConnectorStatus = Schemas['ConnectorStatus']
export type ConnectorOptions = Schemas['ConnectorOptions']
export type MetricaSummary = Schemas['MetricaSummary']
export type MetricaClientIds = Schemas['MetricaClientIds']

export const RUNS_LIMIT = 20

export const connectorsQueryKey = ['connectors'] as const
export const connectorRunsQueryKey = (id: string) => ['connectors', id, 'runs'] as const
export const metricaSummaryQueryKey = ['connectors', 'metrica-summary'] as const

const path = (id: string) => `/api/v1/source/connectors/${encodeURIComponent(id)}`

export const connectorsApi = {
  list: (signal?: AbortSignal) => request<ConnectorList>('/api/v1/source/connectors', { signal }),
  get: (id: string, signal?: AbortSignal) => request<Connector>(path(id), { signal }),
  create: (body: ConnectorCreate) => request<Connector>('/api/v1/source/connectors', { method: 'POST', body }),
  patch: (id: string, body: ConnectorPatch) => request<Connector>(path(id), { method: 'PATCH', body }),
  remove: (id: string) => request<void>(path(id), { method: 'DELETE' }),
  run: (id: string) => request<Connector>(`${path(id)}/run`, { method: 'POST' }),
  resetCursor: (id: string) => request<Connector>(`${path(id)}/reset-cursor`, { method: 'POST' }),
  runs: (id: string, signal?: AbortSignal) =>
    request<{ items: ConnectorRun[] }>(`${path(id)}/runs`, { query: { limit: RUNS_LIMIT }, signal }),
  metrica: (signal?: AbortSignal) => request<MetricaSummary>('/api/v1/source/metrica', { signal }),
  uploadClientIds: (body: MetricaClientIds) =>
    request<{ accepted: number; total: number }>('/api/v1/source/metrica/client-ids', { method: 'POST', body }),
}
