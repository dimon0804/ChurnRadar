import { expect, test, type Page } from '@playwright/test'
import { loginApi } from './helpers'

// Вёрстка не разъезжается на телефоне и ноутбуке. Тест только читает: ничего не меняет в данных.

const PAGES: { path: string; heading: string }[] = [
  { path: '/', heading: 'Обзор' },
  { path: '/customers', heading: 'Клиенты' },
  { path: '/marketing', heading: 'Маркетинг' },
  { path: '/model', heading: 'Модель риска' },
  { path: '/quality', heading: 'Качество модели' },
  { path: '/mail', heading: 'Письма' },
  { path: '/notifications', heading: 'Уведомления' },
  { path: '/users', heading: 'Пользователи' },
  { path: '/data', heading: 'Данные' },
  { path: '/profile', heading: 'Профиль' },
]

const WIDTHS = [
  { name: 'телефон', width: 390, height: 844 },
  { name: 'ноутбук', width: 1280, height: 800 },
]

async function horizontalOverflow(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement
    return { scroll: doc.scrollWidth, client: doc.clientWidth }
  })
}

/** Пары видимых кнопок и ссылок шапки, чьи рамки пересекаются. */
async function headerOverlaps(page: Page) {
  return page.locator('header').evaluate((header) => {
    const visible = [...header.querySelectorAll<HTMLElement>('a, button, [role=status]')].filter((el) => {
      const r = el.getBoundingClientRect()
      return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden' && !el.closest('.sr-only')
    })
    const label = (el: HTMLElement) => el.getAttribute('aria-label') || el.textContent?.trim() || el.tagName
    const hits: string[] = []
    for (let i = 0; i < visible.length; i++)
      for (let j = i + 1; j < visible.length; j++) {
        const a = visible[i]
        const b = visible[j]
        if (a.contains(b) || b.contains(a)) continue
        const ra = a.getBoundingClientRect()
        const rb = b.getBoundingClientRect()
        const x = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left)
        const y = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top)
        if (x > 1 && y > 1) hits.push(`${label(a)} × ${label(b)}`)
      }
    const r = header.getBoundingClientRect()
    const outside = visible.filter((el) => {
      const e = el.getBoundingClientRect()
      return e.left < r.left - 1 || e.right > r.right + 1
    })
    return { hits, outside: outside.map(label) }
  })
}

for (const size of WIDTHS) {
  test.describe(`вёрстка: ${size.name} ${size.width}px`, () => {
    test.use({ viewport: { width: size.width, height: size.height } })

    test.beforeEach(async ({ page }) => {
      await loginApi(page.request)
    })

    for (const p of PAGES) {
      test(`${p.heading}: без горизонтального скролла и наложений в шапке`, async ({ page }) => {
        await page.goto(p.path)
        await expect(page.getByRole('heading', { level: 1, name: p.heading })).toBeVisible()
        // даём графикам и таблицам дорисоваться
        await page.waitForTimeout(800)

        const { scroll, client } = await horizontalOverflow(page)
        expect(scroll, `страница шире экрана на ${scroll - client}px`).toBeLessThanOrEqual(client)

        const { hits, outside } = await headerOverlaps(page)
        expect(hits, 'элементы шапки наезжают друг на друга').toEqual([])
        expect(outside, 'элементы шапки вылезают за её край').toEqual([])

        await expect(page.getByTestId('alerts-bell')).toBeInViewport()
        await expect(page.getByRole('button', { name: /^Профиль:/ })).toBeInViewport()
        if (size.width < 1024) {
          await expect(page.getByTestId('nav-menu')).toBeInViewport()
        } else {
          await expect(page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Клиенты' })).toBeInViewport()
        }
      })
    }

    test('карточка клиента помещается в экран', async ({ page }) => {
      const res = await page.request.get('/api/v1/customers', { params: { page_size: '1', sort: 'expected_loss', order: 'desc' } })
      expect(res.status()).toBe(200)
      const { items } = (await res.json()) as { items: { id: number }[] }
      test.skip(items.length === 0, 'в базе нет клиентов')
      await page.goto(`/customers/${items[0].id}`)
      const sheet = page.getByRole('dialog')
      await expect(sheet).toBeVisible()
      await page.waitForTimeout(800)
      const box = await sheet.boundingBox()
      expect(box).not.toBeNull()
      expect(box!.x).toBeGreaterThanOrEqual(0)
      expect(box!.x + box!.width).toBeLessThanOrEqual(size.width + 1)
      const inner = await sheet.evaluate((el) => {
        const scroller = el.querySelector<HTMLElement>('.overflow-y-auto')
        return scroller ? scroller.scrollWidth - scroller.clientWidth : 0
      })
      expect(inner, 'содержимое карточки шире самой карточки').toBeLessThanOrEqual(0)
    })
  })
}

test.describe('вёрстка: широкий экран 1920px', () => {
  test.use({ viewport: { width: 1920, height: 1080 } })

  // на 2xl у администратора появляются подписи всех восьми разделов — раньше последний наезжал на «онлайн»
  test('подписи разделов не наезжают на правую часть шапки', async ({ page }) => {
    await loginApi(page.request)
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1, name: 'Обзор' })).toBeVisible()
    const { hits, outside } = await headerOverlaps(page)
    expect(hits, 'элементы шапки наезжают друг на друга').toEqual([])
    expect(outside).toEqual([])
  })
})

test.describe('вёрстка: меню на телефоне', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('разделы открываются из выезжающей панели', async ({ page }) => {
    await loginApi(page.request)
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1, name: 'Обзор' })).toBeVisible()
    await page.getByTestId('nav-menu').click()
    const drawer = page.getByTestId('nav-drawer')
    await expect(drawer).toBeVisible()
    await drawer.getByRole('link', { name: 'Клиенты' }).click()
    await expect(drawer).toBeHidden()
    await expect(page.getByRole('heading', { level: 1, name: 'Клиенты' })).toBeVisible()
  })
})
