import { request } from './client'
import type {
  AuthProviders,
  CustomerDetail,
  CustomerPage,
  LoginRequest,
  ModelSettings,
  ModelSettingsPatch,
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

  settings: (signal?: AbortSignal) => request<ModelSettings>('/api/v1/model/settings', { signal }),
  patchSettings: (body: ModelSettingsPatch) =>
    request<ModelSettings>('/api/v1/model/settings', { method: 'PATCH', body }),
  resetSettings: () => request<ModelSettings>('/api/v1/model/settings/reset', { method: 'POST' }),
  settingsHistory: (limit = 50, signal?: AbortSignal) =>
    request<SettingsHistory>('/api/v1/model/settings/history', { query: { limit }, signal }),

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
  summary: ['summary'] as const,
  settings: ['settings'] as const,
  history: ['settings-history'] as const,
  simulator: ['simulator'] as const,
}
