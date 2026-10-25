import { expect, test, type Page } from '@playwright/test'
import { loginApi } from './helpers'
import { layoutProblems } from './layout-check'

// Вёрстка не разъезжается от телефона до широкого экрана. Тест только читает: ничего не меняет в данных.

const PAGES: { path: string; heading: string }[] = [
  { path: '/', heading: 'Обзор' },
  { path: '/customers', heading: 'Клиенты' },
  { path: '/marketing', heading: 'Маркетинг' },
  { path: '/model', heading: 'Модель риска' },
  { path: '/quality', heading: 'Качество модели' },
  { path: '/mail', heading: 'Письма' },
  { path: '/notifications', heading: 'Уведомления' },
  { path: '/users', heading: 'Пользователи' },
  { path: '/login-settings', heading: 'Вход и регистрация' },
  { path: '/data', heading: 'Данные' },
  { path: '/profile', heading: 'Профиль' },
  { path: '/playbooks', heading: 'Сценарии' },
  { path: '/integrations', heading: 'Интеграции' },
  { path: '/audit', heading: 'Журнал действий' },
]

/** Диалоги и выезжающие панели: открываются без изменений в данных. */
const DIALOGS: { name: string; path: string; open: (page: Page) => Promise<void> }[] = [
  { name: 'карточка клиента', path: '/customers/9', open: async () => {} },
  {
    name: 'письмо клиенту',
    path: '/customers/9',
    open: async (page) => page.getByRole('dialog').getByRole('button', { name: /^Написать/ }).first().click(),
  },
  { name: 'новый сценарий', path: '/playbooks', open: async (page) => page.getByRole('button', { name: 'Новый сценарий' }).click() },
  { name: 'новый пользователь', path: '/users', open: async (page) => page.getByRole('button', { name: 'Добавить пользователя' }).click() },
]

const MAIN_NAV = ['Обзор', 'Клиенты', 'Маркетинг', 'Модель', 'Качество модели', 'Письма', 'Сценарии']
const ADMIN_NAV = ['Данные', 'Пользователи', 'Вход и регистрация', 'Интеграции', 'Журнал действий']

/** Подписи основных разделов видны целиком (не только иконки и не обрезаны). */
async function expectMainLabelsVisible(page: Page) {
  const nav = page.getByRole('navigation', { name: 'Разделы' })
  for (const name of MAIN_NAV) {
    const link = nav.getByRole('link', { name, exact: true })
    await expect(link).toBeInViewport()
    const label = link.getByText(name, { exact: true })
    await expect(label).toBeVisible()
    const clipped = await label.evaluate((el) => el.scrollWidth > el.clientWidth + 1 || el.getBoundingClientRect().width < 8)
    expect(clipped, `подпись «${name}» обрезана`).toBe(false)
  }
}

const WIDTHS = [
  { name: 'телефон', width: 390, height: 844 },
  { name: 'планшет', width: 768, height: 1024 },
  { name: 'ноутбук', width: 1280, height: 800 },
  { name: 'широкий экран', width: 1920, height: 1080 },
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
      test(`${p.heading}: без горизонтального скролла и наложений`, async ({ page }) => {
        await page.goto(p.path)
        await expect(page.getByRole('heading', { level: 1, name: p.heading })).toBeVisible()
        // даём графикам и таблицам дорисоваться
        await page.waitForTimeout(800)

        const { scroll, client } = await horizontalOverflow(page)
        expect(scroll, `страница шире экрана на ${scroll - client}px`).toBeLessThanOrEqual(client)
        // кнопки, ссылки и поля всей страницы не наезжают друг на друга и не вылезают из карточек
        expect(await layoutProblems(page), 'вёрстка разъехалась').toEqual([])

        const { hits, outside } = await headerOverlaps(page)
        expect(hits, 'элементы шапки наезжают друг на друга').toEqual([])
        expect(outside, 'элементы шапки вылезают за её край').toEqual([])

        await expect(page.getByTestId('alerts-bell')).toBeInViewport()
        await expect(page.getByRole('button', { name: /^Профиль:/ })).toBeInViewport()
        if (size.width < 1024) {
          await expect(page.getByTestId('nav-menu')).toBeInViewport()
        } else {
          await expect(page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Клиенты' })).toBeInViewport()
          await expect(page.getByRole('navigation', { name: 'Разделы' }).getByRole('button', { name: /^Администрирование/ })).toBeInViewport()
        }
      })
    }

    for (const d of DIALOGS) {
      test(`диалог «${d.name}»: без наложений и вылезаний`, async ({ page }) => {
        await page.goto(d.path)
        await page.locator('main').waitFor()
        await page.waitForTimeout(600)
        await d.open(page)
        await expect(page.getByRole('dialog').last()).toBeVisible()
        await page.waitForTimeout(700)
        expect(await layoutProblems(page, 'dialog'), 'вёрстка диалога разъехалась').toEqual([])
        const { scroll, client } = await horizontalOverflow(page)
        expect(scroll).toBeLessThanOrEqual(client)
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

for (const size of [
  { name: 'ноутбук', width: 1280, height: 800 },
  { name: 'широкий экран', width: 1920, height: 1080 },
]) {
  test.describe(`вёрстка: шапка администратора ${size.width}px`, () => {
    test.use({ viewport: { width: size.width, height: size.height } })

    // служебные разделы свёрнуты в «Администрирование», поэтому подписи основных помещаются целиком
    test('подписи разделов видны и не наезжают на правую часть шапки', async ({ page }) => {
      await loginApi(page.request)
      await page.goto('/')
      await expect(page.getByRole('heading', { level: 1, name: 'Обзор' })).toBeVisible()
      await expectMainLabelsVisible(page)
      const { hits, outside } = await headerOverlaps(page)
      expect(hits, 'элементы шапки наезжают друг на друга').toEqual([])
      expect(outside).toEqual([])
    })

    test('меню «Администрирование» ведёт в служебные разделы и подсвечивает текущий', async ({ page }) => {
      await loginApi(page.request)
      await page.goto('/')
      const nav = page.getByRole('navigation', { name: 'Разделы' })
      for (const name of ADMIN_NAV) await expect(nav.getByRole('link', { name, exact: true })).toHaveCount(0)

      await nav.getByRole('button', { name: /^Администрирование/ }).click()
      const menu = page.getByRole('menu')
      await expect(menu.getByRole('menuitem')).toHaveCount(ADMIN_NAV.length)
      for (const [i, name] of ADMIN_NAV.entries()) await expect(menu.getByRole('menuitem').nth(i)).toContainText(name)
      await menu.getByRole('menuitem', { name: /^Журнал действий/ }).click()
      await expect(page.getByRole('heading', { level: 1, name: 'Журнал действий' })).toBeVisible()
      await expect(menu).toHaveCount(0)
      await expect(nav.getByRole('button', { name: /^Администрирование/ })).toHaveAccessibleName(/Журнал действий/)
      await expectMainLabelsVisible(page)
    })
  })
}

test.describe('вёрстка: меню на телефоне', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('разделы открываются из выезжающей панели', async ({ page }) => {
    await loginApi(page.request)
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1, name: 'Обзор' })).toBeVisible()
    await page.getByTestId('nav-menu').click()
    const drawer = page.getByTestId('nav-drawer')
    await expect(drawer).toBeVisible()
    // в панели все разделы, служебные — отдельной группой «Администрирование»
    for (const name of MAIN_NAV) await expect(drawer.getByRole('link', { name, exact: true })).toBeVisible()
    const admin = drawer.getByRole('group', { name: 'Администрирование' })
    for (const name of ADMIN_NAV) await expect(admin.getByRole('link', { name, exact: true })).toBeVisible()
    await drawer.getByRole('link', { name: 'Клиенты' }).click()
    await expect(drawer).toBeHidden()
    await expect(page.getByRole('heading', { level: 1, name: 'Клиенты' })).toBeVisible()
  })
})
