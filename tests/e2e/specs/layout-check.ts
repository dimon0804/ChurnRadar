import type { Page } from '@playwright/test'

export type LayoutProblem = { kind: 'overlap' | 'out-of-card' | 'hscroll'; what: string }

/**
 * Ищет на странице (или в открытом диалоге) разъехавшуюся вёрстку:
 * - overlap — две видимые кнопки, ссылки или поля пересекаются;
 * - out-of-card — кнопка или поле вылезает за свою карточку;
 * - hscroll — страница шире экрана.
 * Видимой считается только часть элемента внутри обрезающих предков (таблица с прокруткой,
 * панель с overflow), а липкие и фиксированные слои (шапка, подвал диалога) сравниваются
 * только внутри себя: под них по задумке уезжает прокручиваемое содержимое.
 */
export async function layoutProblems(page: Page, scope: 'page' | 'dialog' = 'page'): Promise<LayoutProblem[]> {
  return page.evaluate((onlyDialog) => {
    const problems: { kind: 'overlap' | 'out-of-card' | 'hscroll'; what: string }[] = []
    type Box = { left: number; top: number; right: number; bottom: number; width: number; height: number }

    const clipped = (el: Element): Box => {
      const b = el.getBoundingClientRect()
      const r = { left: b.left, top: b.top, right: b.right, bottom: b.bottom }
      for (let n = el.parentElement; n && n !== document.documentElement; n = n.parentElement) {
        const cs = getComputedStyle(n)
        if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue
        const c = n.getBoundingClientRect()
        if (cs.overflowX !== 'visible') {
          r.left = Math.max(r.left, c.left)
          r.right = Math.min(r.right, c.right)
        }
        if (cs.overflowY !== 'visible') {
          r.top = Math.max(r.top, c.top)
          r.bottom = Math.min(r.bottom, c.bottom)
        }
      }
      return { ...r, width: r.right - r.left, height: r.bottom - r.top }
    }
    const visible = (el: Element) => {
      const r = clipped(el)
      if (r.width < 2 || r.height < 2) return false
      const cs = getComputedStyle(el)
      return cs.visibility !== 'hidden' && cs.opacity !== '0' && !el.closest('.sr-only,[aria-hidden="true"]')
    }
    const label = (el: Element) => {
      const t = el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || el.textContent || el.tagName
      return `${el.tagName.toLowerCase()} «${t.trim().replace(/\s+/g, ' ').slice(0, 40)}»`
    }
    const layer = (el: Element) => {
      for (let n: Element | null = el; n && n !== document.body; n = n.parentElement) {
        const pos = getComputedStyle(n).position
        if (pos === 'sticky' || pos === 'fixed') return n
      }
      return null
    }
    const inside = (a: Box, b: Box) => a.left >= b.left - 1 && a.right <= b.right + 1 && a.top >= b.top - 1 && a.bottom <= b.bottom + 1
    const isField = (el: Element) => el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'

    let root: Element = document.body
    if (onlyDialog) {
      const layers = [...document.querySelectorAll('[role=dialog],[role=alertdialog],[role=menu]')].filter(visible)
      if (layers.length) root = layers[layers.length - 1]!
    }
    const skip = (el: Element) => el.closest('[data-testid=recalc-toast],[data-testid=demo-hint],[role=tooltip]')
    const all = [
      ...root.querySelectorAll(
        'a,button,input:not([type=hidden]),select,textarea,[role=button],[role=switch],[role=tab],[role=combobox],[role=radio],[role=checkbox]',
      ),
    ].filter((el) => visible(el) && !skip(el))
    const controls = all.filter((el) => !all.some((o) => o !== el && o.contains(el) && o.tagName !== 'LABEL'))

    for (let i = 0; i < controls.length; i++)
      for (let j = i + 1; j < controls.length; j++) {
        const a = controls[i]!
        const b = controls[j]!
        if (a.contains(b) || b.contains(a) || layer(a) !== layer(b)) continue
        const ra = clipped(a)
        const rb = clipped(b)
        // кнопка внутри поля (показать пароль, очистить поиск) — так задумано
        if ((isField(a) && inside(rb, ra)) || (isField(b) && inside(ra, rb))) continue
        const x = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left)
        const y = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top)
        if (x > 2 && y > 2) problems.push({ kind: 'overlap', what: `${label(a)} × ${label(b)}` })
      }

    for (const el of controls) {
      const card = el.parentElement?.closest('.rounded-xl.border, .rounded-2xl.border, [role=dialog]')
      if (!card) continue
      let scroller = false
      for (let n = el.parentElement; n && n !== card; n = n.parentElement) {
        if (getComputedStyle(n).overflowX !== 'visible') scroller = true
      }
      if (scroller) continue
      const c = card.getBoundingClientRect()
      const r = el.getBoundingClientRect()
      const out = Math.max(c.left - r.left, r.right - c.right)
      if (out > 1) problems.push({ kind: 'out-of-card', what: `${label(el)} на ${Math.round(out)}px` })
    }

    const doc = document.documentElement
    if (doc.scrollWidth > doc.clientWidth + 1) problems.push({ kind: 'hscroll', what: `шире экрана на ${doc.scrollWidth - doc.clientWidth}px` })
    return problems
  }, scope === 'dialog')
}
