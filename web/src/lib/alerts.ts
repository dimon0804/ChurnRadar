import type { AlertChannel, AlertEventCode, AlertEventDefinition, AlertKind } from '@/api/alerts'

/** Текст на значке колокольчика: до 9 — число, дальше «9+». */
export function badgeText(count: number): string {
  return count > 9 ? '9+' : String(count)
}

/** Подписи записей журнала, включая старые записи до персональных настроек. */
export const KIND_LABEL: Record<AlertKind, string> = {
  loss_growth: 'Рост потери',
  big_customer_high: 'Крупный клиент',
  risk_share: 'Доля под риском',
  high_count_growth: 'Высокий риск',
  stale_data: 'Нет пересчёта',
  high_risk: 'Высокий риск',
  loss_jump: 'Рост потери',
  total_loss_jump: 'Общая потеря',
  bulk: 'Массовое изменение',
  model_drift: 'Модель устарела',
}

/** Подписи личных уведомлений: тип события плюс сводка и проверка канала. */
export function eventLabel(code: string): string {
  if (code === 'digest') return 'Сводка за сутки'
  if (code === 'test') return 'Проверка'
  return KIND_LABEL[code as AlertKind] ?? 'Уведомление'
}

export const CHANNEL_LABEL: Record<AlertChannel, string> = {
  panel: 'В панели',
  email: 'Почта',
  telegram: 'Telegram',
}

const UNIT_SUFFIX: Record<AlertEventDefinition['unit'], string> = {
  pct: '%',
  eur: '€',
  count: 'клиентов',
  min: 'мин',
}

export function unitSuffix(unit: AlertEventDefinition['unit']): string {
  return UNIT_SUFFIX[unit]
}

export function periodLabel(hours: number): string {
  if (hours === 1) return 'за час'
  if (hours === 6) return 'за 6 часов'
  if (hours === 24) return 'за сутки'
  if (hours === 72) return 'за 3 дня'
  if (hours === 168) return 'за неделю'
  return `за ${hours} ч`
}

function num(value: number): string {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value)
}

/** Как будет выглядеть уведомление с этим порогом: пример заголовка для превью в настройках. */
export function previewTitle(code: AlertEventCode, threshold: number, periodH?: number | null): string {
  const t = Number.isFinite(threshold) ? threshold : 0
  switch (code) {
    case 'loss_growth':
      return `Ожидаемая потеря выросла на ${num(t + 2.5)} % ${periodLabel(periodH ?? 24)}`
    case 'big_customer_high':
      return `Крупный клиент #1042 перешёл в высокий риск: ожидаемая потеря €${num(Math.round(t * 1.4))}`
    case 'risk_share':
      return `Под риском ${num(t + 1.3)} % выручки`
    case 'high_count_growth':
      return `В высоком риске на ${Math.round(t) + 3} клиентов больше, чем ${(periodLabel(periodH ?? 24)).replace('за ', '')} назад`
    case 'stale_data':
      return `Оценки не пересчитывались ${Math.round(t) + 5} мин после новых данных`
  }
}

/** Часовые пояса для тихих часов и сводки: Россия плюс текущий пояс браузера. */
export function timezoneOptions(current: string): { value: string; label: string }[] {
  const zones: [string, string][] = [
    ['Europe/Kaliningrad', 'Калининград (UTC+2)'],
    ['Europe/Moscow', 'Москва (UTC+3)'],
    ['Europe/Samara', 'Самара (UTC+4)'],
    ['Asia/Yekaterinburg', 'Екатеринбург (UTC+5)'],
    ['Asia/Omsk', 'Омск (UTC+6)'],
    ['Asia/Novosibirsk', 'Новосибирск (UTC+7)'],
    ['Asia/Irkutsk', 'Иркутск (UTC+8)'],
    ['Asia/Vladivostok', 'Владивосток (UTC+10)'],
    ['Europe/Berlin', 'Берлин'],
    ['UTC', 'UTC'],
  ]
  const options = zones.map(([value, label]) => ({ value, label }))
  if (current && !options.some((o) => o.value === current)) options.unshift({ value: current, label: current })
  return options
}
