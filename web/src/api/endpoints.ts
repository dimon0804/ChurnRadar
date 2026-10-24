import { request } from './client'
import type {
  AuthProviders,
  CallToday,
  CustomerActionState,
  CustomerActionUpdate,
  CustomerDetail,
  CustomerPage,
  LoginRequest,
  ModelQuality,
  ModelSettings,
  ModelSettingsPatch,
  ModelSettingsPreview,
  SettingsHistory,
  SimulatorCommand,
  SimulatorState,
  SnapshotAccepted,
  SourceSnapshot,
  Summary,
  User,
} from './types'

export const api = {
  providers: () => request<AuthProviders>('/api/v1/auth/providers', { allowUnauthorized: true }),
  me: (opts: { allowUnauthorized?: boolean } = {}) => request<User>('/api/v1/auth/me', opts),
  login: (body: LoginRequest) =>
    request<User>('/api/v1/auth/login', { method: 'POST', body, allowUnauthorized: true }),
  logout: () => request<void>('/api/v1/auth/logout', { method: 'POST', allowUnauthorized: true }),

  customers: (query: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    request<CustomerPage>('/api/v1/customers', { query, signal }),
  customer: (id: number, signal?: AbortSignal) => request<CustomerDetail>(`/api/v1/customers/${id}`, { signal }),
  summary: (signal?: AbortSignal) => request<Summary>('/api/v1/analytics/summary', { signal }),

  callToday: (query: { limit?: number; marked?: boolean }, signal?: AbortSignal) =>
    request<CallToday>('/api/v1/call-today', { query, signal }),
  customerAction: (id: number, signal?: AbortSignal) =>
    request<CustomerActionState>(`/api/v1/customers/${id}/action`, { signal }),
  putCustomerAction: (id: number, body: CustomerActionUpdate) =>
    request<CustomerActionState>(`/api/v1/customers/${id}/action`, { method: 'PUT', body }),
  deleteCustomerAction: (id: number) =>
    request<CustomerActionState>(`/api/v1/customers/${id}/action`, { method: 'DELETE' }),

  settings: (signal?: AbortSignal) => request<ModelSettings>('/api/v1/model/settings', { signal }),
  patchSettings: (body: ModelSettingsPatch) =>
    request<ModelSettings>('/api/v1/model/settings', { method: 'PATCH', body }),
  resetSettings: () => request<ModelSettings>('/api/v1/model/settings/reset', { method: 'POST' }),
  previewSettings: (body: ModelSettingsPatch, signal?: AbortSignal) =>
    request<ModelSettingsPreview>('/api/v1/model/settings/preview', { method: 'POST', body, signal }),
  settingsHistory: (limit = 50, signal?: AbortSignal) =>
    request<SettingsHistory>('/api/v1/model/settings/history', { query: { limit }, signal }),
  modelQuality: (signal?: AbortSignal) => request<ModelQuality>('/api/v1/model/quality', { signal }),

  putSnapshot: (body: SourceSnapshot) =>
    request<SnapshotAccepted>('/api/v1/source/snapshot', { method: 'PUT', body }),
  simulator: (signal?: AbortSignal) => request<SimulatorState>('/api/v1/simulator', { signal }),
  putSimulator: (body: SimulatorCommand) =>
    request<SimulatorState>('/api/v1/simulator', { method: 'PUT', body }),
}

export const queryKeys = {
  me: ['me'] as const,
  providers: ['auth-providers'] as const,
  customers: ['customers'] as const,
  customerList: (query: Record<string, unknown>) => ['customers', query] as const,
  customer: (id: number) => ['customer', id] as const,
  customerAll: ['customer'] as const,
  // под префиксами customers/customer: после пересчёта они обновляются вместе со списком и карточкой
  callToday: (marked: boolean) => ['customers', 'call-today', { marked }] as const,
  customerAction: (id: number) => ['customer', id, 'action'] as const,
  summary: ['summary'] as const,
  settings: ['settings'] as const,
  history: ['settings-history'] as const,
  modelQuality: ['model-quality'] as const,
  settingsPreview: (patch: ModelSettingsPatch) => ['settings-preview', patch] as const,
  simulator: ['simulator'] as const,
}
