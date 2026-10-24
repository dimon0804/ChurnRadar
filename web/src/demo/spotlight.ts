/**
 * Как найти элемент на странице, не трогая сами страницы:
 * по CSS-селектору и, если нужно, по тексту, затем подняться к ближайшему контейнеру.
 */
export interface Target {
  selector: string
  text?: string
  closest?: string
}

function normalize(value: string | null | undefined): string {
  return (value ?? '').replace(/\s+/g, ' ').trim().toLowerCase()
}

export function findTarget(target: Target, root: ParentNode = document): Element | null {
  const candidates = Array.from(root.querySelectorAll(target.selector))
  const wanted = target.text ? normalize(target.text) : null
  const hit = wanted
    ? (candidates.find((el) => normalize(el.textContent) === wanted) ??
      candidates.find((el) => normalize(el.textContent).startsWith(wanted)))
    : candidates[0]
  if (!hit) return null
  return target.closest ? (hit.closest(target.closest) ?? hit) : hit
}

export function findTargets(targets: readonly Target[], root: ParentNode = document): Element[] {
  const found: Element[] = []
  for (const t of targets) {
    const el = findTarget(t, root)
    if (el && !found.includes(el)) found.push(el)
  }
  return found
}

/** Ждёт, пока элемент появится (страницы грузятся лениво, данные приходят не сразу). */
export function waitForTarget(target: Target, timeoutMs = 8000, intervalMs = 120): Promise<Element | null> {
  return new Promise((resolve) => {
    const started = Date.now()
    const tick = () => {
      const el = findTarget(target)
      if (el) return resolve(el)
      if (Date.now() - started >= timeoutMs) return resolve(null)
      window.setTimeout(tick, intervalMs)
    }
    tick()
  })
}

export const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms))
