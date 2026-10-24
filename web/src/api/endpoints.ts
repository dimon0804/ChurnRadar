import { request } from './client'
import type {
  AuthProviders,
  Campaign,
  CampaignPreview,
  CampaignRequest,
  ContactToday,
  CustomerActionState,
  CustomerActionUpdate,
  CustomerDetail,
  CustomerPage,
  EmailDraft,
  EmailSendRequest,
  EmailSent,
  MailStats,
  MailTemplate,
  MailTemplatePatch,
  MailTemplates,
  LoginRequest,
  Marketing,
  MarketingSettings,
  MarketingSettingsUpdate,
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
  marketing: (signal?: AbortSignal) => request<Marketing>('/api/v1/analytics/marketing', { signal }),
  putMarketingSettings: (body: MarketingSettingsUpdate) =>
    request<MarketingSettings>('/api/v1/analytics/marketing/settings', { method: 'PUT', body }),

  contactToday: (query: { limit?: number; marked?: boolean }, signal?: AbortSignal) =>
    request<ContactToday>('/api/v1/contact-today', { query, signal }),
  customerAction: (id: number, signal?: AbortSignal) =>
    request<CustomerActionState>(`/api/v1/customers/${id}/action`, { signal }),
  putCustomerAction: (id: number, body: CustomerActionUpdate) =>
    request<CustomerActionState>(`/api/v1/customers/${id}/action`, { method: 'PUT', body }),
  deleteCustomerAction: (id: number) =>
    request<CustomerActionState>(`/api/v1/customers/${id}/action`, { method: 'DELETE' }),

  emailDraft: (id: number, templateCode: string | null, signal?: AbortSignal) =>
    request<EmailDraft>(`/api/v1/customers/${id}/email/draft`, {
      method: 'POST',
      body: { template_code: templateCode },
      signal,
    }),
  sendEmail: (id: number, body: EmailSendRequest) =>
    request<EmailSent>(`/api/v1/customers/${id}/email`, { method: 'POST', body }),
  mailTemplates: (signal?: AbortSignal) => request<MailTemplates>('/api/v1/mail/templates', { signal }),
  putMailTemplate: (code: string, body: MailTemplatePatch) =>
    request<MailTemplate>(`/api/v1/mail/templates/${encodeURIComponent(code)}`, { method: 'PUT', body }),
  resetMailTemplate: (code: string) =>
    request<MailTemplate>(`/api/v1/mail/templates/${encodeURIComponent(code)}/reset`, { method: 'POST' }),
  previewCampaign: (body: CampaignRequest, signal?: AbortSignal) =>
    request<CampaignPreview>('/api/v1/mail/campaigns/preview', { method: 'POST', body, signal }),
  createCampaign: (body: CampaignRequest) =>
    request<Campaign>('/api/v1/mail/campaigns', { method: 'POST', body }),
  campaign: (id: number, signal?: AbortSignal) => request<Campaign>(`/api/v1/mail/campaigns/${id}`, { signal }),
  campaigns: (signal?: AbortSignal) => request<{ items: Campaign[] }>('/api/v1/mail/campaigns', { signal }),
  mailStats: (signal?: AbortSignal) => request<MailStats>('/api/v1/mail/stats', { signal }),

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
  contactToday: (marked: boolean) => ['customers', 'contact-today', { marked }] as const,
  customerAction: (id: number) => ['customer', id, 'action'] as const,
  // не под префиксом customer: пересчёт не должен перезаписывать текст, который сотрудник правит
  emailDraft: (id: number, template: string | null) => ['email-draft', id, template] as const,
  // письма меняют очередь и статистику; после пересчёта «помогло» тоже может измениться
  mailStats: ['customers', 'mail-stats'] as const,
  mailTemplates: ['mail-templates'] as const,
  campaigns: ['mail-campaigns'] as const,
  campaign: (id: number) => ['mail-campaigns', id] as const,
  summary: ['summary'] as const,
  // под префиксом summary: после пересчёта обновляется вместе со сводкой
  marketing: ['summary', 'marketing'] as const,
  settings: ['settings'] as const,
  history: ['settings-history'] as const,
  modelQuality: ['model-quality'] as const,
  settingsPreview: (patch: ModelSettingsPatch) => ['settings-preview', patch] as const,
  simulator: ['simulator'] as const,
}
