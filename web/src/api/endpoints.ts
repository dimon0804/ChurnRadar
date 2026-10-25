import { request } from './client'
import type {
  AuthProviders,
  Campaign,
  CampaignPreview,
  CampaignRequest,
  ContactToday,
  CustomerActionState,
  CustomerActionUpdate,
  CustomerCharts,
  CustomerDetail,
  CustomerPage,
  EmailDraft,
  EmailSendRequest,
  EmailSent,
  MailStats,
  MailTemplate,
  MailSettings,
  MailSettingsUpdate,
  MailTemplatePatch,
  MailTemplatePreview,
  MailTemplatePreviewRequest,
  MailTemplates,
  LoginRequest,
  Marketing,
  MarketingSettings,
  MarketingSettingsUpdate,
  ModelQuality,
  ModelSettings,
  ModelSettingsPatch,
  ModelSettingsPreview,
  ReasonDetail,
  RiskDistribution,
  RiskScatter,
  SettingsCompare,
  SettingsHistory,
  SimulatorCommand,
  SimulatorState,
  SnapshotAccepted,
  SourceSnapshot,
  Summary,
  SummaryHistory,
  AuditList,
  ManagedUser,
  User,
  UserCreate,
  UserCreated,
  UserList,
  UserUpdate,
  ApiTokenCreate,
  ApiTokenCreated,
  ApiTokenList,
  AuditPage,
  AuditQuery,
  ReportSchedule,
  ReportScheduleUpdate,
  WeeklyReportSent,
} from './types'

/** PDF скачивает сам браузер по обычной ссылке, cookie сессии уходит вместе с запросом. */
export const WEEKLY_REPORT_PDF_URL = '/api/v1/reports/weekly?format=pdf'

export const api = {
  providers: () => request<AuthProviders>('/api/v1/auth/providers', { allowUnauthorized: true }),
  me: (opts: { allowUnauthorized?: boolean } = {}) => request<User>('/api/v1/auth/me', opts),
  login: (body: LoginRequest) =>
    request<User>('/api/v1/auth/login', { method: 'POST', body, allowUnauthorized: true }),
  logout: () => request<void>('/api/v1/auth/logout', { method: 'POST', allowUnauthorized: true }),
  updateProfile: (body: { email: string | null }) =>
    request<User>('/api/v1/auth/me', { method: 'PATCH', body }),
  changePassword: (body: { current_password: string; new_password: string }) =>
    request<void>('/api/v1/auth/me/password', { method: 'POST', body }),

  users: (q: string, signal?: AbortSignal) => request<UserList>('/api/v1/auth/users', { query: { q }, signal }),
  createUser: (body: UserCreate) => request<UserCreated>('/api/v1/auth/users', { method: 'POST', body }),
  updateUser: (id: string, body: UserUpdate) =>
    request<{ user: ManagedUser }>(`/api/v1/auth/users/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  resetUserPassword: (id: string, password: string | null) =>
    request<{ temporary_password: string } | undefined>(`/api/v1/auth/users/${encodeURIComponent(id)}/password`, {
      method: 'POST',
      body: { password },
    }),
  deleteUser: (id: string) => request<void>(`/api/v1/auth/users/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  userAudit: (limit = 50, signal?: AbortSignal) =>
    request<AuditList>('/api/v1/auth/audit', { query: { limit }, signal }),

  customers: (query: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    request<CustomerPage>('/api/v1/customers', { query, signal }),
  customer: (id: number, signal?: AbortSignal) => request<CustomerDetail>(`/api/v1/customers/${id}`, { signal }),
  summary: (signal?: AbortSignal) => request<Summary>('/api/v1/analytics/summary', { signal }),
  distribution: (signal?: AbortSignal) => request<RiskDistribution>('/api/v1/analytics/distribution', { signal }),
  scatter: (query: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    request<RiskScatter>('/api/v1/analytics/scatter', { query, signal }),
  reason: (code: string, scope: 'main' | 'any', signal?: AbortSignal) =>
    request<ReasonDetail>(`/api/v1/analytics/reasons/${encodeURIComponent(code)}`, { query: { scope }, signal }),
  summaryHistory: (signal?: AbortSignal) => request<SummaryHistory>('/api/v1/analytics/history', { signal }),
  customerCharts: (id: number, signal?: AbortSignal) =>
    request<CustomerCharts>(`/api/v1/customers/${id}/charts`, { signal }),
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
  previewMailTemplate: (code: string, body: MailTemplatePreviewRequest, signal?: AbortSignal) =>
    request<MailTemplatePreview>(`/api/v1/mail/preview/${encodeURIComponent(code)}`, { method: 'POST', body, signal }),
  mailSettings: (signal?: AbortSignal) => request<MailSettings>('/api/v1/mail/settings', { signal }),
  putMailSettings: (body: MailSettingsUpdate) => request<MailSettings>('/api/v1/mail/settings', { method: 'PUT', body }),
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
  rollbackSettings: (version: number) =>
    request<ModelSettings>('/api/v1/model/settings/rollback', { method: 'POST', body: { version } }),
  compareSettings: (from: number, to: number, signal?: AbortSignal) =>
    request<SettingsCompare>('/api/v1/model/settings/compare', { query: { from, to }, signal }),
  modelQuality: (signal?: AbortSignal) => request<ModelQuality>('/api/v1/model/quality', { signal }),

  putSnapshot: (body: SourceSnapshot) =>
    request<SnapshotAccepted>('/api/v1/source/snapshot', { method: 'PUT', body }),
  simulator: (signal?: AbortSignal) => request<SimulatorState>('/api/v1/simulator', { signal }),
  putSimulator: (body: SimulatorCommand) =>
    request<SimulatorState>('/api/v1/simulator', { method: 'PUT', body }),

  apiTokens: (signal?: AbortSignal) => request<ApiTokenList>('/api/v1/auth/tokens', { signal }),
  createApiToken: (body: ApiTokenCreate) =>
    request<ApiTokenCreated>('/api/v1/auth/tokens', { method: 'POST', body }),
  revokeApiToken: (id: string) =>
    request<void>(`/api/v1/auth/tokens/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  audit: (query: AuditQuery, signal?: AbortSignal) => request<AuditPage>('/api/v1/audit', { query, signal }),

  reportSchedule: (signal?: AbortSignal) => request<ReportSchedule>('/api/v1/reports/schedule', { signal }),
  putReportSchedule: (body: ReportScheduleUpdate) =>
    request<ReportSchedule>('/api/v1/reports/schedule', { method: 'PUT', body }),
  sendWeeklyReport: (recipients?: string[]) =>
    request<WeeklyReportSent>('/api/v1/reports/weekly/send', {
      method: 'POST',
      body: recipients ? { recipients } : {},
    }),
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
  customerCharts: (id: number) => ['customer', id, 'charts'] as const,
  scatter: (query: Record<string, unknown>) => ['customers', 'scatter', query] as const,
  // не под префиксом customer: пересчёт не должен перезаписывать текст, который сотрудник правит
  emailDraft: (id: number, template: string | null) => ['email-draft', id, template] as const,
  // письма меняют очередь и статистику; после пересчёта «помогло» тоже может измениться
  mailStats: ['customers', 'mail-stats'] as const,
  mailTemplates: ['mail-templates'] as const,
  // под префиксом customers: после пересчёта пример письма берёт свежие данные клиента
  mailPreview: (code: string, subject: string, body: string) => ['customers', 'mail-preview', code, subject, body] as const,
  mailSettings: ['mail-settings'] as const,
  campaigns: ['mail-campaigns'] as const,
  campaign: (id: number) => ['mail-campaigns', id] as const,
  summary: ['summary'] as const,
  // под префиксом summary: после пересчёта обновляется вместе со сводкой
  marketing: ['summary', 'marketing'] as const,
  distribution: ['summary', 'distribution'] as const,
  summaryHistory: ['summary', 'history'] as const,
  reason: (code: string, scope: string) => ['summary', 'reason', code, scope] as const,
  settings: ['settings'] as const,
  history: ['settings-history'] as const,
  settingsCompare: (from: number, to: number) => ['settings-history', 'compare', from, to] as const,
  modelQuality: ['model-quality'] as const,
  settingsPreview: (patch: ModelSettingsPatch) => ['settings-preview', patch] as const,
  simulator: ['simulator'] as const,
  users: ['users'] as const,
  userList: (q: string) => ['users', 'list', q] as const,
  userAudit: ['users', 'audit'] as const,
  apiTokens: ['api-tokens'] as const,
  audit: ['audit'] as const,
  auditPage: (query: AuditQuery) => ['audit', query] as const,
  reportSchedule: ['report-schedule'] as const,
}
