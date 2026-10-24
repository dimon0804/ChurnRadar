/** Шаблоны писем на клиенте: предпросмотр с примером и проверка подстановок до сохранения. */

const PLACEHOLDER = /\{\{\s*([a-z_]+)\s*\}\}/g

/** Пример клиента для предпросмотра шаблона в редакторе. */
export const SAMPLE_VALUES: Record<string, string> = {
  email: 'user-0009@example.test',
  service: 'service-0019',
  date: '24 октября',
  amount: '€8,55 в час',
  balance: '€915,06',
  company: 'ChurnRadar',
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
