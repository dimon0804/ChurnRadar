import { request } from './client'
import type { components } from './schema'

/** Сценарии удержания: правило «если — то», контрольная группа и эффект (services/api, app/playbooks.py). */
type Schemas = components['schemas']

export type Playbook = Schemas['Playbook']
export type PlaybookInput = Schemas['PlaybookInput']
export type PlaybookConditions = Schemas['PlaybookConditions']
export type PlaybookAction = Schemas['PlaybookAction']
export type PlaybookPreset = Schemas['PlaybookPreset']
export type PlaybookList = Schemas['PlaybookList']
export type PlaybookPreview = Schemas['PlaybookPreview']
export type PlaybookRunResult = Schemas['PlaybookRunResult']
export type PlaybookRun = Schemas['PlaybookRun']
export type PlaybookHit = Schemas['PlaybookHit']
export type PlaybookUplift = Schemas['PlaybookUplift']
export type PlaybookUpliftWindow = Schemas['PlaybookUpliftWindow']
export type PlaybookUpliftMetric = Schemas['PlaybookUpliftMetric']

export const playbookKeys = {
  // под префиксом customers: после пересчёта список, журнал и эффект обновляются вместе с клиентами
  all: ['customers', 'playbooks'] as const,
  list: ['customers', 'playbooks', 'list'] as const,
  preview: (body: PlaybookInput, id: number | null) => ['playbook-preview', id, body] as const,
  runs: (id: number) => ['customers', 'playbooks', id, 'runs'] as const,
  hits: (id: number) => ['customers', 'playbooks', id, 'hits'] as const,
  uplift: (id: number) => ['customers', 'playbooks', id, 'uplift'] as const,
}

export const playbooksApi = {
  list: (signal?: AbortSignal) => request<PlaybookList>('/api/v1/playbooks', { signal }),
  create: (body: PlaybookInput) => request<Playbook>('/api/v1/playbooks', { method: 'POST', body }),
  update: (id: number, body: PlaybookInput) => request<Playbook>(`/api/v1/playbooks/${id}`, { method: 'PUT', body }),
  remove: (id: number) => request<void>(`/api/v1/playbooks/${id}`, { method: 'DELETE' }),
  preview: (body: PlaybookInput, id: number | null, signal?: AbortSignal) =>
    request<PlaybookPreview>('/api/v1/playbooks/preview', {
      method: 'POST',
      body,
      query: { playbook_id: id ?? undefined },
      signal,
    }),
  run: (id: number) => request<PlaybookRunResult>(`/api/v1/playbooks/${id}/run`, { method: 'POST' }),
  runs: (id: number, signal?: AbortSignal) =>
    request<{ items: PlaybookRun[] }>(`/api/v1/playbooks/${id}/runs`, { signal }),
  hits: (id: number, signal?: AbortSignal) =>
    request<{ items: PlaybookHit[] }>(`/api/v1/playbooks/${id}/hits`, { signal }),
  uplift: (id: number, signal?: AbortSignal) => request<PlaybookUplift>(`/api/v1/playbooks/${id}/uplift`, { signal }),
}
