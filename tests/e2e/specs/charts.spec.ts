import { expect, test, type Locator, type Page } from '@playwright/test'
import { loginApi, loginUi } from './helpers'

// Графики и кликабельные причины: графики отрисованы, клик по причине и по столбцу гистограммы
// ведёт в отфильтрованный список, точка на графике клиентов открывает карточку.
test.describe.configure({ mode: 'serial' })

type Distribution = { buckets: { from: number; to: number; customers: number }[]; levels: { level: string; customers: number }[] }

/** Нажимает на самый высокий элемент из найденных: столбец гистограммы с клиентами. */
async function clickLargest(items: Locator) {
  await expect(items.first()).toBeVisible()
  const count = await items.count()
  let best = -1
  let height = 1
  for (let i = 0; i < count; i++) {
    const box = await items.nth(i).boundingBox()
    if (box && box.height > height) {
      best = i
      height = box.height
    }
  }
  expect(best, 'есть хотя бы один ненулевой столбец').toBeGreaterThanOrEqual(0)
  await items.nth(best).click()
}

async function chartRendered(page: Page, testId: string) {
  const card = page.getByTestId(testId)
  await expect(card).toBeVisible()
  await expect(card.locator('[data-chart] svg.recharts-surface').first()).toBeVisible()
  await expect(card.locator('[data-chart]').first()).toHaveAttribute('aria-label', /\S/)
}

test('API графиков: распределение сходится со сводкой, история и причина отдаются', async ({ request }) => {
  await loginApi(request)
  const d = (await (await request.get('/api/v1/analytics/distribution')).json()) as Distribution
  expect(d.buckets).toHaveLength(10)
  const summary = await (await request.get('/api/v1/analytics/summary')).json()
  const scored = d.buckets.reduce((s, b) => s + b.customers, 0)
  expect(scored).toBe(summary.customers_total - summary.by_level.unknown)

  const history = await request.get('/api/v1/analytics/history')
  expect(history.status()).toBe(200)
  expect(Array.isArray((await history.json()).items)).toBe(true)

  const top = summary.top_reasons.find((r: { customers: number }) => r.customers > 0)
  test.skip(!top, 'в данных нет причин риска')
  const reason = await (await request.get(`/api/v1/analytics/reasons/${top.code}`)).json()
  expect(reason.customers).toBe(top.customers)
  const list = await (await request.get('/api/v1/customers', { params: { reason: top.code, page_size: '1' } })).json()
  expect(list.total).toBe(top.customers)
})

test('обзор: графики отрисованы, клик по причине открывает отфильтрованный список', async ({ page, request }) => {
  await loginApi(request)
  await loginUi(page)
  await page.goto('/')
  await chartRendered(page, 'chart-score-histogram')
  await chartRendered(page, 'chart-revenue-by-level')
  await expect(page.getByTestId('chart-loss-by-reason')).toBeVisible()
  await expect(page.getByTestId('chart-metrics-history')).toBeVisible()

  // «Главные причины риска»: первая причина — ссылка
  const reasonLink = page.locator('main a[data-reason]').first()
  await expect(reasonLink).toBeVisible()
  const code = await reasonLink.getAttribute('data-reason')
  await reasonLink.click()
  await expect(page).toHaveURL(new RegExp(`/customers\\?reason=${code}`))
  await expect(page.getByTestId('chip-reason')).toBeVisible()
  await expect(page.getByTestId('reason-panel')).toBeVisible()
  await expect(page.getByTestId('reason-write-all')).toBeEnabled()

  const api = await (await request.get('/api/v1/customers', { params: { reason: code!, page_size: '1' } })).json()
  await expect(page.getByText(new RegExp(`из\\s${api.total.toLocaleString('ru-RU').replace(/\s/g, '\\s')}$`)).first()).toBeVisible()

  // сброс чипа возвращает полный список
  await page.getByTestId('chip-reason').getByRole('button').click()
  await expect(page).not.toHaveURL(/reason=/)
  await expect(page.getByTestId('reason-panel')).toHaveCount(0)
})

test('клик по столбцу гистограммы открывает клиентов из этого диапазона', async ({ page, request }) => {
  await loginApi(request)
  await loginUi(page)
  await page.goto('/')
  await clickLargest(page.locator('[data-testid="chart-score-histogram"] path.recharts-rectangle'))
  await expect(page).toHaveURL(/\/customers\?score_min=\d+&score_max=\d+/)
  const url = new URL(page.url())
  const min = Number(url.searchParams.get('score_min'))
  const max = Number(url.searchParams.get('score_max'))
  expect(max - min).toBe(10)
  await expect(page.getByTestId('chip-score')).toContainText(`${min}–${max}`)

  const res = await request.get('/api/v1/customers', { params: { score_min: String(min), score_max: String(max), page_size: '200' } })
  const body = (await res.json()) as { items: { risk_score: number }[]; total: number }
  expect(body.total).toBeGreaterThan(0)
  for (const c of body.items) {
    expect(c.risk_score).toBeGreaterThanOrEqual(min)
    if (max < 100) expect(c.risk_score).toBeLessThan(max)
  }
})

test('клиенты: график «риск × потеря», точка открывает карточку с графиками', async ({ page }) => {
  await loginUi(page)
  await page.goto('/customers')
  await page.getByTestId('customers-view-chart').click()
  await expect(page).toHaveURL(/view=chart/)
  const scatter = page.getByTestId('risk-scatter')
  await expect(scatter.locator('svg.recharts-surface')).toBeVisible()
  const dot = scatter.locator('.recharts-scatter-symbol').first()
  await expect(dot).toBeVisible()
  await dot.click()
  await expect(page).toHaveURL(/\/customers\/\d+/)
  const sheet = page.getByRole('dialog')
  await expect(sheet.getByTestId('chart-payments')).toBeVisible()
  await expect(sheet.getByTestId('chart-score-waterfall')).toBeVisible()

  // причина в карточке ведёт в список с этой причиной
  const reason = sheet.locator('a[data-reason]').first()
  const code = await reason.getAttribute('data-reason')
  await reason.click()
  await expect(page).toHaveURL(new RegExp(`reason=${code}`))
  await expect(page.getByTestId('chip-reason')).toBeVisible()
})

test('маркетинг и модель: графики на месте', async ({ page }) => {
  await loginUi(page)
  await page.goto('/marketing')
  await chartRendered(page, 'chart-mrr-segments')
  await chartRendered(page, 'chart-ltv-segments')
  await chartRendered(page, 'chart-payment-terms')
  await expect(page.getByTestId('chart-funnel')).toBeVisible()
  const lossLink = page.getByTestId('mkt-loss-by-reason').locator('a[data-reason]').first()
  await lossLink.click()
  await expect(page).toHaveURL(/\/customers\?reason=/)

  await page.goto('/model')
  await chartRendered(page, 'chart-feature-loss')
  // «Таблица» по кнопке — у каждого графика есть текстовая версия
  await page.getByTestId('chart-feature-loss').getByRole('button', { name: 'Таблица' }).click()
  await expect(page.getByTestId('chart-feature-loss').getByRole('table')).toBeVisible()
})
