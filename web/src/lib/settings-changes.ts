import type { SettingsChange } from '@/api/types'
import { formatDecimal } from './format'

export type ChangeItem = SettingsChange['changes'][number]

function fmt(v: unknown): string {
  if (typeof v === 'number') return formatDecimal(v, 1)
  if (typeof v === 'boolean') return v ? 'вкл.' : 'выкл.'
  if (v === null || v === undefined) return '—'
  return String(v)
}

/**
 * Одна строка журнала по-человечески: «Порог высокого риска: 65 → 70».
 * titles — названия признаков по code, groupTitles — названия групп признаков.
 */
export function describeChange(
  change: ChangeItem,
  titles: ReadonlyMap<string, string>,
  groupTitles: ReadonlyMap<string, string> = new Map(),
): string {
  const parts = change.path.split('.')
  if (parts[0] === 'weights' && parts.length >= 3) {
    const code = parts.slice(1, -1).join('.')
    const field = parts.at(-1)
    const title = titles.get(code) ?? code
    if (field === 'enabled') return `«${title}» ${change.new ? 'включён' : 'выключен'}`
    return `«${title}»: ${fmt(change.old)} → ${fmt(change.new)}`
  }
  if (parts[0] === 'groups' && parts.length >= 3) {
    const code = parts.slice(1, -1).join('.')
    const field = parts.at(-1)
    const title = groupTitles.get(code) ?? code
    if (field === 'enabled') return `Группа «${title}» ${change.new ? 'включена' : 'выключена'}`
    if (field === 'cap') return `Потолок группы «${title}»: ${fmt(change.old)} → ${fmt(change.new)}`
  }
  if (change.path === 'calibrated')
    return change.new ? 'Деньги считаются по калиброванной вероятности' : 'Деньги считаются по сырому score'
  if (change.path === 'thresholds.medium') return `Порог среднего риска: ${fmt(change.old)} → ${fmt(change.new)}`
  if (change.path === 'thresholds.high') return `Порог высокого риска: ${fmt(change.old)} → ${fmt(change.new)}`
  if (change.path === 'horizon_days') return `Горизонт прогноза: ${fmt(change.old)} → ${fmt(change.new)} дн.`
  if (change.path === 'reset' || change.path === '*') return 'Сброс к значениям по умолчанию'
  if (change.path === 'rollback') return `Откат к версии ${fmt(change.new)}`
  return `${change.path}: ${fmt(change.old)} → ${fmt(change.new)}`
}

/** Пара версий для сравнения: всегда от старой к новой, null — пока выбрано меньше двух. */
export function comparePair(selected: readonly number[]): { from: number; to: number } | null {
  const uniq = [...new Set(selected)]
  if (uniq.length < 2) return null
  const [a, b] = uniq.slice(-2) as [number, number]
  return a < b ? { from: a, to: b } : { from: b, to: a }
}

/** Выбор версии для сравнения: максимум две, третья вытесняет самую раннюю отмеченную. */
export function toggleCompare(selected: readonly number[], version: number): number[] {
  if (selected.includes(version)) return selected.filter((v) => v !== version)
  return [...selected, version].slice(-2)
}
