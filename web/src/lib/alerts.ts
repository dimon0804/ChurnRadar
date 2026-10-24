import type { AlertItem } from '@/api/alerts'

const SEEN_KEY = 'cr-alerts-seen'

/** id последнего просмотренного оповещения; в приватном режиме — null, счётчик просто покажет всё. */
export function readSeenId(): number | null {
  try {
    const raw = localStorage.getItem(SEEN_KEY)
    const value = raw === null ? NaN : Number(raw)
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}

export function writeSeenId(id: number) {
  try {
    localStorage.setItem(SEEN_KEY, String(id))
  } catch {
    // приватный режим: счётчик не запомнится между сессиями
  }
}

/** Сколько оповещений новее просмотренного. Первый визит (seen = null) — новых нет, чтобы не пугать архивом. */
export function countUnread(items: readonly AlertItem[], seenId: number | null): number {
  if (seenId === null) return 0
  return items.filter((item) => item.id > seenId).length
}

/** Текст на значке: до 9 — число, дальше «9+». */
export function badgeText(count: number): string {
  return count > 9 ? '9+' : String(count)
}

export const KIND_LABEL: Record<AlertItem['kind'], string> = {
  high_risk: 'Высокий риск',
  loss_jump: 'Рост потери',
  total_loss_jump: 'Общая потеря',
  bulk: 'Массовое изменение',
}
