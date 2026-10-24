import type { CustomerAction, CustomerActionUpdate, CustomerSort, Recommendation, SortOrder } from '@/api/types'
import { formatDate, formatMoney } from '@/lib/format'
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

/** Вся очередь «Кому позвонить» в том же порядке, что в блоке на обзоре. */
export const CALL_LIST_CSV_URL = exportCsvUrl({ sort: 'expected_loss', order: 'desc', worklist: true })

export const SNOOZE_OPTIONS = [
  { days: 1, label: 'До завтра' },
  { days: 3, label: 'На 3 дня' },
  { days: 7, label: 'На неделю' },
  { days: 30, label: 'На месяц' },
] as const

export function doneUpdate(note?: string): CustomerActionUpdate {
  const text = note?.trim()
  return text ? { status: 'done', note: text } : { status: 'done' }
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

export type ActionTone = 'done' | 'snoozed' | 'returned' | 'expired'

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
  if (action.status === 'done') {
    if (action.returned) {
      return {
        tone: 'returned',
        label: 'Риск вырос после отметки',
        detail: `Отметили «Сделано» ${when}, но с тех пор ожидаемая потеря или уровень риска выросли`,
      }
    }
    return { tone: 'done', label: 'Сделано', detail: `Отметили ${when}` }
  }
  const until = formatDate(action.snoozed_until, { short: true, now })
  if (!action.active) {
    return { tone: 'expired', label: 'Срок отложки прошёл', detail: `Откладывали до ${until} (${when})` }
  }
  return { tone: 'snoozed', label: `Отложено до ${until}`, detail: `Отложили ${when}` }
}
