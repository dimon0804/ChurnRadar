/**
 * Короткие подписи причин и действий для таблицы и сводки.
 *
 * Сервер отдаёт по каждой причине длинное объяснение с цифрами (title) и, если есть, действие
 * (action). В таблице длинное объяснение не помещается, поэтому показываем название признака
 * (как на странице «Модель») и действие, а полный текст — во всплывающей подсказке.
 */

/** Названия признаков модели. Если сервер добавит новый признак, название возьмём из настроек или сводки. */
export const FEATURE_TITLE: Record<string, string> = {
  prolong_wait: 'Ждёт оплаты',
  autoprolong_off_expiring: 'Автопродление выключено',
  insufficient_funds: 'Не хватит денег на продление',
  suspended: 'Сервер выключен',
  failed_payments: 'Платежи не проходят',
  short_commitment: 'Короткий период оплаты',
  churn_history: 'Уже отказывался от услуг',
  inactivity: 'Давно не платил',
  bonus_dependency: 'Держится на бонусах',
  withdraw_pressure: 'Может вывести деньги',
  all_services_gone: 'Уже ушёл',
  no_live_services: 'Нет действующих услуг',
  no_paid_services: 'Нет платных услуг',
  panel_absence: 'Давно не заходил в панель',
  web_activity_drop: 'Реже заходит на сайт',
  cancel_page_views: 'Смотрел страницу удаления',
}

/** Что делать с клиентами без оценки — сервер отдаёт это только в карточке, в списке действия нет. */
const UNKNOWN_ACTION: Record<string, string> = {
  all_services_gone: 'Предложить вернуться',
  no_live_services: 'Помочь с первым заказом',
}

export interface ReasonLike {
  code: string
  title: string
  /** Короткое название от сервера (новое поле API); без него берём словарь. */
  label?: string | null
  action?: string | null
}

export type TitleLookup = ReadonlyMap<string, string> | Record<string, string>

function lookup(map: TitleLookup | undefined, code: string): string | undefined {
  if (!map) return undefined
  if (map instanceof Map) return map.get(code)
  return (map as Record<string, string>)[code]
}

/** Короткое название причины: из переданного словаря (настройки, сводка), из встроенного, иначе — сам текст. */
export function shortReasonTitle(reason: ReasonLike, titles?: TitleLookup): string {
  const label = reason.label?.trim()
  if (label) return label
  const known = lookup(titles, reason.code) ?? FEATURE_TITLE[reason.code]
  if (known) return known
  const text = reason.title.trim()
  // «Нет оценки: …» и подобное — берём часть до двоеточия, если она короткая
  const head = text.split(/[:—]/)[0]?.trim() ?? text
  return head.length > 0 && head.length <= 40 ? head : text
}

/** Действие по причине, если сервер его прислал (или известно для клиентов без оценки). */
export function reasonAction(reason: ReasonLike): string | null {
  const action = reason.action?.trim()
  if (action) return action
  return UNKNOWN_ACTION[reason.code] ?? null
}

/** Полный текст для подсказки: объяснение и действие. */
export function reasonTooltip(reason: ReasonLike): string {
  const action = reasonAction(reason)
  return action ? `${reason.title}\nЧто сделать: ${action}` : reason.title
}

const GONE_STATUSES = new Set(['deleted', 'blocked'])

/**
 * Клиент ушёл: оценки нет, потому что все его услуги удалены или заблокированы.
 * Для таких клиентов это кандидат на возврат, а не «нет данных».
 */
export function isChurned(customer: {
  risk_level: string
  reasons?: ReadonlyArray<{ code: string; label?: string | null }> | null
  services?: ReadonlyArray<{ status: string }> | null
}): boolean {
  if (customer.risk_level !== 'unknown') return false
  if (customer.reasons?.some((r) => r.code === 'all_services_gone' || r.label?.trim() === 'Уже ушёл')) return true
  const services = customer.services ?? []
  return services.length > 0 && services.every((s) => GONE_STATUSES.has(s.status))
}
