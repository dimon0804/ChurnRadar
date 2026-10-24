import type {
  CampaignPreview,
  CustomerAction,
  CustomerActionLogEntry,
  CustomerActionUpdate,
  CustomerSort,
  MailStats,
  Recommendation,
  SortOrder,
} from '@/api/types'
import { countLabel, formatDate, formatMoney, formatShare } from '@/lib/format'
import type { CustomersState } from '@/lib/url-state'

export const EXPORT_CSV_PATH = '/api/v1/customers/export.csv'

export interface ExportQuery {
  sort?: CustomerSort
  order?: SortOrder
  levels?: readonly string[]
  search?: string
  worklist?: boolean
}

/** Ссылка на CSV с теми же фильтрами и сортировкой, что в таблице. Страница и размер не нужны. */
export function exportCsvUrl(q: ExportQuery = {}): string {
  const params = new URLSearchParams()
  if (q.sort) params.set('sort', q.sort)
  if (q.order) params.set('order', q.order)
  if (q.levels && q.levels.length > 0) params.set('level', q.levels.join(','))
  const search = q.search?.trim()
  if (search) params.set('search', search)
  if (q.worklist) params.set('worklist', 'true')
  const qs = params.toString()
  return qs ? `${EXPORT_CSV_PATH}?${qs}` : EXPORT_CSV_PATH
}

export function exportUrlFromState(state: CustomersState): string {
  return exportCsvUrl({ sort: state.sort, order: state.order, levels: state.levels, search: state.search })
}

/** Вся очередь «С кем связаться» в том же порядке, что в блоке на обзоре. */
export const CONTACT_LIST_CSV_URL = exportCsvUrl({ sort: 'expected_loss', order: 'desc', worklist: true })

export const SNOOZE_OPTIONS = [
  { days: 1, label: 'До завтра' },
  { days: 3, label: 'На 3 дня' },
  { days: 7, label: 'На неделю' },
  { days: 30, label: 'На месяц' },
] as const

/** Клиент ответил или вопрос закрыт. */
export function resolvedUpdate(note?: string): CustomerActionUpdate {
  const text = note?.trim()
  return text ? { status: 'resolved', note: text } : { status: 'resolved' }
}

/** Написали клиенту сами, не из панели. Срок ожидания ответа — по настройке сервера. */
export function emailedUpdate(note?: string): CustomerActionUpdate {
  const text = note?.trim()
  return text ? { status: 'emailed', note: text } : { status: 'emailed' }
}

export function snoozeUpdate(days: number): CustomerActionUpdate {
  return { status: 'snoozed', snooze_days: days }
}

/** «Сбережёт до €343» — только если у действия есть денежный эффект. */
export function savingText(action: Recommendation | null | undefined): string | null {
  const saving = action?.expected_saving
  if (typeof saving !== 'number' || saving <= 0) return null
  return `Сбережёт до ${formatMoney(saving, { cents: false })}`
}

export type ActionTone = 'emailed' | 'resolved' | 'snoozed' | 'returned' | 'expired'

export interface ActionStatus {
  tone: ActionTone
  /** Короткая подпись для бейджа. */
  label: string
  /** Кто и когда отметил — для подсказки и карточки. */
  detail: string
}

/** Как показать отметку. null — отметки нет. */
export function actionStatus(action: CustomerAction | null | undefined, now: Date = new Date()): ActionStatus | null {
  if (!action) return null
  const who = action.updated_by ? `, ${action.updated_by}` : ''
  const when = `${formatDate(action.updated_at, { short: true, now })}${who}`
  const until = formatDate(action.until, { short: true, now })
  switch (action.return_reason) {
    case 'risk_grew':
      return {
        tone: 'returned',
        label: 'Риск вырос после отметки',
        detail: `Отметка от ${when}, но с тех пор ожидаемая потеря или уровень риска выросли`,
      }
    case 'no_reply':
      return {
        tone: 'returned',
        label: 'Ответа нет — напомнить',
        detail: `Письмо ушло ${when}, ждали ответа до ${until}`,
      }
    case 'snooze_over':
      return { tone: 'expired', label: 'Срок отложки прошёл', detail: `Откладывали до ${until} (${when})` }
    default:
      break
  }
  if (action.status === 'emailed') {
    return { tone: 'emailed', label: 'Письмо отправлено', detail: `Письмо ушло ${when}, ждём ответа до ${until}` }
  }
  if (action.status === 'resolved') return { tone: 'resolved', label: 'Решено', detail: `Отметили ${when}` }
  return { tone: 'snoozed', label: `Отложено до ${until}`, detail: `Отложили ${when}` }
}

const LOG_LABEL: Record<CustomerActionLogEntry['status'], string> = {
  emailed: 'Письмо отправлено',
  resolved: 'Решено',
  snoozed: 'Отложили',
  cleared: 'Вернули в очередь',
}

/** Строка журнала: «Письмо отправлено: «Пополните баланс…»», «Отложили до 27 окт». */
export function logLine(entry: CustomerActionLogEntry): string {
  if (entry.status === 'emailed') return entry.note ? `${LOG_LABEL.emailed}: «${entry.note}»` : LOG_LABEL.emailed
  const what = entry.status === 'snoozed' ? `${LOG_LABEL.snoozed} до ${formatDate(entry.until)}` : LOG_LABEL[entry.status]
  return entry.note ? `${what}: «${entry.note}»` : what
}

/** «За 30 дней 12 писем, помогло 5 клиентам из 10 (50 %)». null — писем ещё не было. */
export function statsLine(stats: MailStats | null | undefined): string | null {
  if (!stats || stats.sent === 0) return null
  const letters = countLabel(stats.sent, ['письмо', 'письма', 'писем'])
  const helped =
    stats.customers > 0
      ? `помогло ${stats.helped} из ${countLabel(stats.customers, ['клиента', 'клиентов', 'клиентов'])} (${formatShare(stats.helped_share ?? 0, 0)})`
      : null
  return [`За ${stats.period_days} дней ${letters}`, helped].filter(Boolean).join(', ')
}

/** Итог предпросмотра рассылки одной фразой. */
export function campaignSummary(p: CampaignPreview, skipDays: number): string {
  const parts = [`Уйдёт ${countLabel(p.to_send, ['письмо', 'письма', 'писем'])}`]
  if (p.skipped_recent > 0) {
    parts.push(
      `${countLabel(p.skipped_recent, ['клиента', 'клиентов', 'клиентов'])} пропустим: им уже писали за ${countLabel(skipDays, ['день', 'дня', 'дней'])}`,
    )
  }
  if (p.skipped_no_email > 0) parts.push(`у ${countLabel(p.skipped_no_email, ['клиента', 'клиентов', 'клиентов'])} нет email`)
  return parts.join('; ')
}

/** Подписи шаблонов на случай, если список шаблонов ещё не загрузился. */
export const TEMPLATE_TITLE: Record<string, string> = {
  insufficient_funds: 'Не хватит денег на продление',
  autoprolong_off_expiring: 'Автопродление выключено',
  prolong_wait: 'Ждёт оплаты',
  suspended: 'Сервер выключен',
  failed_payments: 'Платежи не проходят',
  inactivity: 'Давно не платил',
  win_back: 'Клиент ушёл',
  general: 'Общий',
}
