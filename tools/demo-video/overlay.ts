/**
 * Слой поверх любой страницы: глава в углу, субтитры внизу, подсветка элементов и нарисованный курсор.
 * Функция сериализуется в браузер (addInitScript / evaluate), поэтому должна быть самодостаточной:
 * никаких внешних переменных и импортов.
 */
export function installOverlay(): void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any
  if (w.top !== w) return
  if (w.__demo) return

  const ACCENT = '#ffc53d'
  const css = `
    #__demo-root, #__demo-root * { box-sizing: border-box; }
    #__demo-root { position: fixed; inset: 0; pointer-events: none; z-index: 2147483646;
      font-family: "Segoe UI", "Inter", system-ui, -apple-system, Roboto, Arial, sans-serif; }
    #__demo-dim { position: fixed; inset: 0; width: 100vw; height: 100vh; opacity: 0; transition: opacity .45s ease; }
    #__demo-dim.on { opacity: 1; }
    .__demo-ring { position: fixed; border: 3px solid ${ACCENT}; border-radius: 12px;
      box-shadow: 0 0 0 4px rgba(255,197,61,.28), 0 0 28px rgba(255,197,61,.55);
      opacity: 0; transition: opacity .45s ease; }
    .__demo-ring.on { opacity: 1; }
    #__demo-chapter { position: fixed; top: 14px; right: 18px; max-width: 520px;
      display: flex; align-items: center; gap: 10px; padding: 9px 16px 9px 12px; border-radius: 999px;
      background: rgba(12,14,20,.86); color: #fff; font-size: 17px; font-weight: 600; letter-spacing: .01em;
      box-shadow: 0 6px 24px rgba(0,0,0,.28); opacity: 0; transform: translateY(-8px);
      transition: opacity .5s ease, transform .5s ease; }
    #__demo-chapter.on { opacity: 1; transform: none; }
    #__demo-chapter.on.under { opacity: 0; }
    #__demo-chapter .n { display: inline-flex; align-items: center; justify-content: center; min-width: 28px; height: 28px;
      padding: 0 8px; border-radius: 999px; background: ${ACCENT}; color: #1b1300; font-size: 15px; font-weight: 700; }
    #__demo-sub { position: fixed; left: 50%; bottom: 26px; transform: translate(-50%, 10px);
      width: min(1180px, calc(100vw - 80px)); padding: 13px 28px 14px; border-radius: 16px;
      background: rgba(10,12,18,.84); backdrop-filter: blur(6px); color: #fff;
      font-size: 24px; line-height: 1.4; font-weight: 500; text-align: center; text-wrap: balance;
      box-shadow: 0 10px 40px rgba(0,0,0,.35); opacity: 0;
      transition: opacity .4s ease, transform .4s ease, top .45s ease, bottom .45s ease; }
    #__demo-sub.on { opacity: 1; transform: translate(-50%, 0); }
    #__demo-sub.top { bottom: auto; top: 66px; }
    #__demo-cursor { position: fixed; left: 0; top: 0; width: 22px; height: 22px; margin: -11px 0 0 -11px;
      border-radius: 50%; background: rgba(255,255,255,.96); border: 2.5px solid #111;
      box-shadow: 0 2px 10px rgba(0,0,0,.45); opacity: 0; transition: opacity .3s ease; will-change: transform; }
    #__demo-cursor.on { opacity: 1; }
    .__demo-ripple { position: fixed; width: 18px; height: 18px; margin: -9px 0 0 -9px; border-radius: 50%;
      border: 3px solid ${ACCENT}; animation: __demo-ripple .6s ease-out forwards; }
    @keyframes __demo-ripple { from { transform: scale(.6); opacity: 1; } to { transform: scale(3.2); opacity: 0; } }
  `

  let root: HTMLDivElement
  let dim: SVGSVGElement
  let mask: SVGMaskElement
  let chapter: HTMLDivElement
  let sub: HTMLDivElement
  let cursor: HTMLDivElement
  let rings: HTMLDivElement[] = []
  let targets: Element[] = []
  let subText = ''
  let cursorPos = { x: -40, y: -40 }

  function build() {
    if (document.getElementById('__demo-root')) return
    const style = document.createElement('style')
    style.textContent = css
    root = document.createElement('div')
    root.id = '__demo-root'
    root.appendChild(style)
    const NS = 'http://www.w3.org/2000/svg'
    dim = document.createElementNS(NS, 'svg') as SVGSVGElement
    dim.id = '__demo-dim'
    const defs = document.createElementNS(NS, 'defs')
    mask = document.createElementNS(NS, 'mask') as SVGMaskElement
    mask.id = '__demo-mask'
    defs.appendChild(mask)
    dim.appendChild(defs)
    const shade = document.createElementNS(NS, 'rect')
    shade.setAttribute('width', '100%')
    shade.setAttribute('height', '100%')
    shade.setAttribute('fill', 'rgba(8,10,16,0.55)')
    shade.setAttribute('mask', 'url(#__demo-mask)')
    dim.appendChild(shade)
    root.appendChild(dim)
    chapter = document.createElement('div')
    chapter.id = '__demo-chapter'
    root.appendChild(chapter)
    sub = document.createElement('div')
    sub.id = '__demo-sub'
    root.appendChild(sub)
    cursor = document.createElement('div')
    cursor.id = '__demo-cursor'
    root.appendChild(cursor)
    ;(document.body ?? document.documentElement).appendChild(root)
    placeCursor(cursorPos.x, cursorPos.y)
  }

  function ready(fn: () => void) {
    if (document.body) {
      build()
      fn()
    } else {
      document.addEventListener('DOMContentLoaded', () => {
        build()
        fn()
      }, { once: true })
    }
  }

  function placeCursor(x: number, y: number) {
    cursorPos = { x, y }
    if (cursor) cursor.style.transform = `translate(${x}px, ${y}px)`
  }

  // подсветка следует за элементами каждый кадр: страница скроллится, данные догружаются
  function frame() {
    if (root && !root.isConnected) (document.body ?? document.documentElement).appendChild(root)
    const vh = window.innerHeight
    const rects: DOMRect[] = []
    if (targets.length && mask) {
      const NS = 'http://www.w3.org/2000/svg'
      while (mask.firstChild) mask.removeChild(mask.firstChild)
      const base = document.createElementNS(NS, 'rect')
      base.setAttribute('width', '100%')
      base.setAttribute('height', '100%')
      base.setAttribute('fill', 'white')
      mask.appendChild(base)
      targets.forEach((el, i) => {
        const r = el.getBoundingClientRect()
        rects.push(r)
        const pad = 8
        const x = r.left - pad
        const y = r.top - pad
        const wdt = Math.max(0, r.width + pad * 2)
        const hgt = Math.max(0, r.height + pad * 2)
        const hole = document.createElementNS(NS, 'rect')
        hole.setAttribute('x', String(x))
        hole.setAttribute('y', String(y))
        hole.setAttribute('width', String(wdt))
        hole.setAttribute('height', String(hgt))
        hole.setAttribute('rx', '12')
        hole.setAttribute('fill', 'black')
        mask.appendChild(hole)
        const ring = rings[i]
        if (ring) {
          ring.style.left = `${x}px`
          ring.style.top = `${y}px`
          ring.style.width = `${wdt}px`
          ring.style.height = `${hgt}px`
        }
      })
    }
    const cursorOn = cursor && cursor.classList.contains('on')
    const hits = (box: DOMRect, r: { left: number; right: number; top: number; bottom: number }) =>
      r.left < box.right && r.right > box.left && r.top < box.bottom && r.bottom > box.top
    const cur = cursorOn ? { left: cursorPos.x - 14, right: cursorPos.x + 14, top: cursorPos.y - 14, bottom: cursorPos.y + 14 } : null
    const near = cursorOn ? { left: cursorPos.x - 60, right: cursorPos.x + 60, top: cursorPos.y - 40, bottom: cursorPos.y + 40 } : null
    if (sub) {
      // субтитры не закрывают то, о чём речь: цель или курсор внизу — плашка уезжает наверх
      const lowest = rects.reduce((m, r) => Math.max(m, r.bottom), 0)
      const highest = rects.reduce((m, r) => Math.min(m, r.top), Infinity)
      const targetLow = rects.length > 0 && lowest > vh - 170 && highest > 230
      const cursorLow = Boolean(cur && cur.bottom > vh - 150 && cur.left > (window.innerWidth - 1180) / 2 - 20 && cur.right < (window.innerWidth + 1180) / 2 + 20)
      sub.classList.toggle('top', targetLow || cursorLow)
    }
    if (chapter) {
      const box = chapter.getBoundingClientRect()
      const under = rects.some((r) => hits(box, r) && r.height < vh * 0.5) || Boolean(near && hits(box, near))
      chapter.classList.toggle('under', under)
    }
    requestAnimationFrame(frame)
  }

  const api = {
    chapter(title: string, n?: number) {
      ready(() => {
        if (!title) {
          chapter.classList.remove('on')
          return
        }
        chapter.innerHTML = ''
        if (n) {
          const badge = document.createElement('span')
          badge.className = 'n'
          badge.textContent = String(n)
          chapter.appendChild(badge)
        }
        const t = document.createElement('span')
        t.textContent = title
        chapter.appendChild(t)
        chapter.classList.add('on')
      })
    },
    say(text: string, instant?: boolean) {
      ready(() => {
        if (text === subText && sub.classList.contains('on')) return
        subText = text
        if (!text) {
          sub.classList.remove('on')
          return
        }
        if (instant || !sub.classList.contains('on')) {
          sub.textContent = text
          sub.classList.add('on')
          return
        }
        // плавная смена: гасим, меняем текст, зажигаем
        sub.classList.remove('on')
        window.setTimeout(() => {
          if (subText !== text) return
          sub.textContent = text
          sub.classList.add('on')
        }, 260)
      })
    },
    spot(els: Element[]) {
      ready(() => {
        api.unspot(true)
        targets = els.filter(Boolean)
        rings = targets.map(() => {
          const ring = document.createElement('div')
          ring.className = '__demo-ring'
          root.appendChild(ring)
          return ring
        })
        requestAnimationFrame(() => {
          dim.classList.add('on')
          rings.forEach((r) => r.classList.add('on'))
        })
      })
    },
    unspot(instant?: boolean) {
      ready(() => {
        const old = rings
        targets = []
        rings = []
        dim.classList.remove('on')
        old.forEach((r) => {
          r.classList.remove('on')
          window.setTimeout(() => r.remove(), instant ? 0 : 450)
        })
      })
    },
    cursorTo(x: number, y: number, ms: number) {
      ready(() => {
        cursor.classList.add('on')
        cursor.style.transition = `opacity .3s ease, transform ${ms}ms cubic-bezier(.45,.05,.25,1)`
        placeCursor(x, y)
      })
    },
    cursorHide() {
      ready(() => cursor.classList.remove('on'))
    },
    ripple(x: number, y: number) {
      ready(() => {
        const r = document.createElement('div')
        r.className = '__demo-ripple'
        r.style.left = `${x}px`
        r.style.top = `${y}px`
        root.appendChild(r)
        window.setTimeout(() => r.remove(), 700)
      })
    },
    restore(state: { chapter: string; n?: number; text: string; cursor?: { x: number; y: number } | null }) {
      ready(() => {
        api.chapter(state.chapter, state.n)
        api.say(state.text, true)
        if (state.cursor) {
          cursor.style.transition = 'opacity .3s ease'
          placeCursor(state.cursor.x, state.cursor.y)
          cursor.classList.add('on')
        }
      })
    },
  }
  w.__demo = api
  ready(() => requestAnimationFrame(frame))
}
