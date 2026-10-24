/** Шаблоны писем на клиенте: подпись к примеру и проверка подстановок до сохранения. */

import type { MailTemplatePreview } from '@/api/types'

const PLACEHOLDER = /\{\{\s*([a-z_]+)\s*\}\}/g

/** Подпись над примером письма: чей это пример и подходит ли шаблон этому клиенту. */
export function previewCaption(p: MailTemplatePreview): string {
  if (p.customer_id === null) return 'Оценённых клиентов с такой причиной нет — вместо данных стоят заглушки'
  const who = `клиент ${p.customer_id}${p.to ? `, ${p.to}` : ''}`
  return p.applicable
    ? `Так письмо увидит клиент (настоящие данные: ${who})`
    : `Пример на данных: ${who}. Такой причины у него нет — при рассылке ему уйдёт письмо по его причине`
}

export function renderTemplate(text: string, values: Record<string, string>): string {
  return text.replace(PLACEHOLDER, (whole, name: string) => values[name] ?? whole)
}

/** Подстановки, которых сервер не знает: их нужно исправить до сохранения. */
export function unknownPlaceholders(text: string, allowed: readonly string[]): string[] {
  const found = new Set<string>()
  for (const m of text.matchAll(PLACEHOLDER)) {
    const name = m[1]
    if (name && !allowed.includes(name)) found.add(name)
  }
  return [...found].sort()
}

/** Вставка подстановки в позицию курсора. Возвращает новый текст и позицию после вставки. */
export function insertAt(text: string, start: number, end: number, insert: string): { text: string; cursor: number } {
  const s = Math.max(0, Math.min(start, text.length))
  const e = Math.max(s, Math.min(end, text.length))
  return { text: text.slice(0, s) + insert + text.slice(e), cursor: s + insert.length }
}
