import type { components, operations } from './schema'

type Schemas = components['schemas']

export type User = Schemas['User']
export type Role = User['roles'][number]
export type RiskLevel = Schemas['RiskLevel']
export type Reason = Schemas['Reason']
export type CustomerSummary = Schemas['CustomerSummary']
export type CustomerPage = Schemas['CustomerPage']
export type CustomerDetail = Schemas['CustomerDetail']
export type ServiceRisk = Schemas['ServiceRisk']
export type ServiceStatus = Schemas['ServiceStatus']
export type PaymentTerm = Schemas['PaymentTerm']
export type FeatureContribution = Schemas['FeatureContribution']
export type Transaction = Schemas['Transaction']
export type Summary = Schemas['Summary']
export type TimelinePoint = Schemas['TimelinePoint']
export type ModelSettings = Schemas['ModelSettings']
export type ModelSettingsPatch = Schemas['ModelSettingsPatch']
export type FeatureWeight = Schemas['FeatureWeight']
export type SettingsChange = Schemas['SettingsChange']
export type ModelSettingsPreview = Schemas['ModelSettingsPreview']
export type PreviewTotals = Schemas['PreviewTotals']
export type PreviewCustomerChange = Schemas['PreviewCustomerChange']
export type SourceSnapshot = Schemas['SourceSnapshot']
export type SnapshotAccepted = Schemas['SnapshotAccepted']
export type SimulatorState = Schemas['SimulatorState']
export type SimulatorCommand = Schemas['SimulatorCommand']
export type ScoresUpdatedEvent = Schemas['ScoresUpdatedEvent']
export type SettingsUpdatedEvent = Schemas['SettingsUpdatedEvent']
export type SourceUpdatedEvent = Schemas['SourceUpdatedEvent']
export type ApiErrorBody = Schemas['Error']
export type LoginRequest = Schemas['LoginRequest']

export type AuthProviders =
  operations['authProviders']['responses']['200']['content']['application/json']
export type SettingsHistory =
  operations['modelSettingsHistory']['responses']['200']['content']['application/json']
export type CustomersQuery = NonNullable<operations['listCustomers']['parameters']['query']>
export type CustomerSort = NonNullable<CustomersQuery['sort']>
export type SortOrder = NonNullable<CustomersQuery['order']>
