import { expect, test } from '@playwright/test'
import {
  METRICS_TOKEN,
  compose,
  composeServices,
  listCustomers,
  loginApi,
  loginUi,
  originHeaders,
  sampleSnapshot,
  waitForStableText,
} from './helpers'

// Сценарий защиты: каждый шаг — пункт приёмки кейса.
test.describe.configure({ mode: 'serial' })

test.afterAll(async ({ request }) => {
  // вернуть стенд в исходное состояние для следующих прогонов
  await loginApi(request)
  await request.post('/api/v1/model/settings/reset', { headers: originHeaders })
  await request.put('/api/v1/source/snapshot', { data: sampleSnapshot(), headers: originHeaders })
})

test('1. без входа ничего не открывается', async ({ page, request }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.goto('/customers?sort=email')
  await expect(page).toHaveURL(/\/login\?next=%2Fcustomers/)

  for (const path of ['/api/v1/customers', '/api/v1/model/settings', '/api/v1/analytics/summary', '/api/v1/stream']) {
    const res = await request.get(path)
    expect(res.status(), path).toBe(401)
    expect((await res.json()).error.code).toBeTruthy()
  }
  const spoofed = await request.get('/api/v1/customers', { headers: { 'X-User-Id': '1', 'X-User-Roles': 'admin' } })
  expect(spoofed.status()).toBe(401)
})

test('2. вход по паролю ведёт на обзор с цифрами', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('Логин').fill('admin')
  await page.getByLabel('Пароль', { exact: true }).fill('wrong-password')
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page.getByText(/неверный логин или пароль/i)).toBeVisible()

  await loginUi(page)
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByTestId('kpi-high').locator('[data-role=value]')).toHaveText(/\d/)
  await expect(page.getByTestId('kpi-expected-loss').locator('[data-role=value]')).toHaveText(/€/)
})

const SORTS = [
  { field: 'id', header: 'ID' },
  { field: 'email', header: 'Email' },
  { field: 'balance', header: 'Баланс' },
  { field: 'total_replenished', header: 'Пополнено всего' },
] as const

test('3. сортировка по ID, email, балансу и пополнениям в обе стороны', async ({ page }) => {
  await loginUi(page)
  await page.goto('/customers?page_size=100')
  const rows = page.getByRole('table', { name: 'Клиенты под риском' }).locator('tbody tr[aria-label]')
  await expect(rows.first()).toBeVisible()

  for (const { field, header } of SORTS) {
    for (const order of ['asc', 'desc'] as const) {
      const th = page.getByRole('columnheader', { name: new RegExp(`^${header}`) })
      // кликаем по заголовку, пока не встанет нужное направление
      for (let i = 0; i < 3 && (await th.getAttribute('aria-sort')) !== (order === 'asc' ? 'ascending' : 'descending'); i++) {
        await th.getByRole('button').click()
      }
      await expect(th).toHaveAttribute('aria-sort', order === 'asc' ? 'ascending' : 'descending')
      await expect(page).toHaveURL(new RegExp(`sort=${field}`))
      await expect(page).toHaveURL(new RegExp(`order=${order}`))

      const api = await listCustomers(page.request, { sort: field, order, page_size: '100' })
      const values = api.items.map((c) => c[field])
      const sorted = [...values].sort((a, b) =>
        typeof a === 'string' ? a.localeCompare(b as string, 'en') : (a as number) - (b as number),
      )
      if (order === 'desc') sorted.reverse()
      expect(values, `${field} ${order} из API`).toEqual(sorted)

      const expected = api.items.map((c) => `Клиент ${c.email || c.id}`)
      await expect
        .poll(async () => rows.evaluateAll((els) => els.map((e) => e.getAttribute('aria-label'))))
        .toEqual(expected)
    }
  }
})

test('4. клиент под высоким риском: причины и деньги', async ({ page }) => {
  await loginUi(page)
  const high = await listCustomers(page.request, { level: 'high', sort: 'risk_score', order: 'desc' })
  expect(high.items.length, 'в тестовых данных есть клиенты высокого риска').toBeGreaterThan(0)
  const id = high.items[0].id

  const detail = await (await page.request.get(`/api/v1/customers/${id}`)).json()
  expect(detail.reasons.length).toBeGreaterThan(0)
  expect(detail.money.explanation).toBeTruthy()

  await page.goto('/customers?level=high')
  await page.getByRole('row', { name: `Клиент ${high.items[0].email}` }).click()
  await expect(page).toHaveURL(new RegExp(`/customers/${id}`))
  await expect(page.getByText(detail.reasons[0].title).first()).toBeVisible()
  await expect(page.getByText(detail.money.explanation).first()).toBeVisible()
})

test('5–6. коэффициент меняет оценки без перезагрузки и переживает рестарт', async ({ browser }) => {
  test.setTimeout(240_000)
  const context = await browser.newContext()
  const watcher = await context.newPage()
  await loginUi(watcher)
  const loss = watcher.getByTestId('kpi-expected-loss').locator('[data-role=value]')
  await expect(loss).toHaveText(/€/)
  const before = await waitForStableText(watcher, '[data-testid=kpi-expected-loss] [data-role=value]')

  // самый весомый признак клиента с наибольшим риском
  const top = (await listCustomers(watcher.request, { sort: 'risk_score', order: 'desc' })).items[0]
  const detail = await (await watcher.request.get(`/api/v1/customers/${top.id}`)).json()
  const feature = [...detail.features].sort((a, b) => b.contribution - a.contribution)[0]
  const settings = await (await watcher.request.get('/api/v1/model/settings')).json()
  const weight = settings.weights.find((w: { code: string }) => w.code === feature.code)
  const target = weight.min

  const editor = await context.newPage()
  await editor.goto('/model')
  const input = editor.getByRole('textbox', { name: `Вес: ${weight.title}` })
  await input.fill(String(target))
  await input.blur()
  await expect(editor.getByText(/версия\s+\d+/i).first()).toBeVisible()

  // первая вкладка не перезагружалась: значение обновилось через SSE
  const navigations: string[] = []
  watcher.on('framenavigated', (f) => f === watcher.mainFrame() && navigations.push(f.url()))
  await expect(loss).not.toHaveText(before, { timeout: 30_000 })
  expect(navigations, 'обзор не перезагружался').toEqual([])

  const saved = await (await watcher.request.get('/api/v1/model/settings')).json()
  expect(saved.weights.find((w: { code: string }) => w.code === feature.code).weight).toBe(target)

  compose('restart', ...composeServices())
  await expect
    .poll(async () => (await watcher.request.get('/api/v1/model/settings')).status(), { timeout: 120_000 })
    .toBe(200)
  const afterRestart = await (await watcher.request.get('/api/v1/model/settings')).json()
  expect(afterRestart.version).toBe(saved.version)
  expect(afterRestart.weights.find((w: { code: string }) => w.code === feature.code).weight).toBe(target)
  await context.close()
})

test('7. новый снимок данных обновляет таблицу сама', async ({ page }) => {
  await loginUi(page)
  await page.goto('/customers?sort=id&order=asc')
  const snapshot = sampleSnapshot()
  const account = snapshot.accounts[0]
  account.balance = 1234567
  account.totalReplenished = Math.max(account.totalReplenished, account.balance)

  const row = page.getByRole('row', { name: `Клиент ${account.email}` })
  await expect(row).toBeVisible()
  await expect(row).not.toContainText(/12\s345,67/)

  const res = await page.request.put('/api/v1/source/snapshot', { data: snapshot, headers: originHeaders })
  expect(res.status()).toBe(202)
  await expect(row).toContainText(/12\s345,67/, { timeout: 30_000 })
})

test('8. /metrics: с токеном показатели совпадают с панелью, без токена 401', async ({ request }) => {
  expect((await request.get('/metrics')).status()).toBe(401)
  expect((await request.get('/metrics', { headers: { Authorization: 'Bearer wrong' } })).status()).toBe(401)

  const res = await request.get('/metrics', { headers: { Authorization: `Bearer ${METRICS_TOKEN}` } })
  expect(res.status()).toBe(200)
  const text = await res.text()
  for (const name of [
    'churn_customers_by_risk_level',
    'churn_expected_loss_eur',
    'churn_exposure_eur',
    'churn_services_at_risk',
    'churn_model_version',
    'churn_last_scored_timestamp_seconds',
  ]) {
    expect(text, name).toContain(name)
  }

  await loginApi(request)
  const summary = await (await request.get('/api/v1/analytics/summary')).json()
  const metric = (re: RegExp) => Number(text.match(re)?.[1])
  expect(metric(/^churn_customers_by_risk_level\{level="high"\}\s+(\S+)/m)).toBe(summary.by_level.high)
  expect(metric(/^churn_expected_loss_eur\s+(\S+)/m)).toBeCloseTo(summary.expected_loss / 100, 1)
})

test('9. Swagger открывается', async ({ page }) => {
  await loginUi(page)
  const res = await page.goto('/api/v1/docs')
  expect(res?.status()).toBe(200)
  await expect(page.locator('body')).toContainText(/ChurnRadar|swagger/i)
})

test('аналитик видит панель, но не может менять модель', async ({ request }) => {
  await loginApi(request, { username: 'analyst', password: 'analyst' })
  expect((await request.get('/api/v1/customers')).status()).toBe(200)
  const res = await request.patch('/api/v1/model/settings', { data: { horizon_days: 14 }, headers: originHeaders })
  expect(res.status()).toBe(403)
})
