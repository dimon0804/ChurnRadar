/** Поля, где Shift+D — это просто заглавная буква, а не команда. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!target || typeof (target as Element).closest !== 'function') return false
  const el = target as HTMLElement
  if (el.isContentEditable) return true
  return el.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="textbox"]') !== null
}

interface KeyLike {
  key: string
  code?: string
  shiftKey: boolean
  ctrlKey: boolean
  altKey: boolean
  metaKey: boolean
  repeat?: boolean
  target: EventTarget | null
}

/**
 * Shift+D открывает пульт. Смотрим и на физическую клавишу (code),
 * чтобы сочетание работало в русской раскладке, где та же клавиша даёт «В».
 */
export function isDemoHotkey(event: KeyLike): boolean {
  if (!event.shiftKey || event.ctrlKey || event.altKey || event.metaKey || event.repeat) return false
  const isD = event.code === 'KeyD' || event.key === 'D' || event.key === 'd'
  if (!isD) return false
  return !isEditableTarget(event.target)
}

/** ?demo=1 (или ?demo без значения) открывает пульт при загрузке. */
export function hasDemoParam(search: string): boolean {
  const value = new URLSearchParams(search).get('demo')
  return value !== null && value !== '0' && value !== 'false'
}
