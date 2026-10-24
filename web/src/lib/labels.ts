import type { PaymentTerm, RiskLevel, Role, ServiceStatus, Transaction } from '@/api/types'

export const RISK_LABEL: Record<RiskLevel, string> = {
  high: 'Высокий',
  medium: 'Средний',
  low: 'Низкий',
  unknown: 'Нет оценки',
}

export const RISK_LABEL_LONG: Record<RiskLevel, string> = {
  high: 'Высокий риск',
  medium: 'Средний риск',
  low: 'Низкий риск',
  unknown: 'Нет оценки',
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: 'Администратор',
  marketer: 'Маркетолог',
  analyst: 'Аналитик',
}

export const SERVICE_STATUS_LABEL: Record<ServiceStatus, string> = {
  active: 'Активна',
  suspended: 'Приостановлена',
  prolong_wait: 'Ждёт продления',
  deleted: 'Удалена',
  blocked: 'Заблокирована',
}

export const PAYMENT_TERM_LABEL: Record<PaymentTerm, string> = {
  hour: 'час',
  week: 'неделю',
  month: 'месяц',
  quarter_year: 'квартал',
  half_year: 'полгода',
  year: 'год',
}

export const TX_TYPE_LABEL: Record<Transaction['type'], string> = {
  prolong: 'Продление',
  order: 'Заказ',
}

export const TX_STATUS_LABEL: Record<Transaction['status'], string> = {
  performed: 'Проведена',
  not_performed: 'Не проведена',
}

const GROUP_LABEL: Record<string, string> = {
  renewal: 'Продление',
  finance: 'Деньги и баланс',
  behavior: 'Поведение',
  activity: 'Активность',
  payment: 'Платежи',
  usage: 'Использование',
  other: 'Прочее',
}

export function groupLabel(group: string): string {
  const known = GROUP_LABEL[group]
  if (known) return known
  const text = group.replace(/[_-]+/g, ' ').trim()
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : 'Прочее'
}

export function labelOf<K extends string>(map: Record<K, string>, key: string | null | undefined, fallback = '—'): string {
  if (!key) return fallback
  return (map as Record<string, string>)[key] ?? key
}
