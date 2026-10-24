import { expect, test, type APIRequestContext } from '@playwright/test'
import { METRICS_TOKEN, loginApi, loginUi, originHeaders, waitForStableText } from './helpers'

// Страница «Маркетинг»: цифры на странице, в API и в /metrics одни и те же.
test.describe.configure({ mode: 'serial' })

type Marketing = {
  mrr: number
  arpu: number
  ltv: number
  churn: { customer_monthly: number | null; revenue_monthly: number | null }
  unit_economics: { margin: number; cac: number | null; ltv_cac: number | null; payback_months: number | null }
  settings: { margin: number; cac: number | null }
}

async function marketing(request: APIRequestContext): Promise<Marketing> {
  const res = await request.get('/api/v1/analytics/marketing')
  expect(res.status()).toBe(200)
  return (await res.json()) as Marketing
}

const euros = (cents: number) => String(Math.round(cents / 100))
const digits = (text: string) => text.replace(/[^\d]/g, '')
const percent = (share: number) => `${(Math.round(share * 1000) / 10).toFixed(1).replace('.', ',').replace(/,0$/, '')}`

let saved: { margin: number; cac: number | null } | null = null

test.afterAll(async ({ request }) => {
  if (!saved) return
  await loginApi(request)
  await request.put('/api/v1/analytics/marketing/settings', { data: saved, headers: originHeaders })
})

test('без входа маркетинговых метрик не видно', async ({ request }) => {
  expect((await request.get('/api/v1/analytics/marketing')).status()).toBe(401)
  const put = await request.put('/api/v1/analytics/marketing/settings', { data: { margin: 0.5 }, headers: originHeaders })
  expect(put.status()).toBe(401)
})

test('/metrics и страница «Маркетинг» показывают те же числа, что API', async ({ page, request }) => {
  await loginApi(request)
  const m = await marketing(request)

  const res = await request.get('/metrics', { headers: { Authorization: `Bearer ${METRICS_TOKEN}` } })
  expect(res.status()).toBe(200)
  const text = await res.text()
  const metric = (name: string) => Number(text.match(new RegExp(`^${name}\\s+(\\S+)`, 'm'))?.[1])
  expect(metric('churn_mrr_eur')).toBeCloseTo(m.mrr / 100, 2)
  expect(metric('churn_arpu_eur')).toBeCloseTo(m.arpu / 100, 2)
  expect(metric('churn_ltv_eur')).toBeCloseTo(m.ltv / 100, 2)
  expect(metric('churn_customer_churn_rate')).toBeCloseTo(m.churn.customer_monthly ?? 0, 6)
  expect(metric('churn_revenue_churn_rate')).toBeCloseTo(m.churn.revenue_monthly ?? 0, 6)

  await loginUi(page)
  await page.getByRole('link', { name: 'Маркетинг' }).click()
  await expect(page).toHaveURL(/\/marketing$/)
  const value = (id: string) => page.getByTestId(id).locator('[data-role=value]')
  await expect(value('mkt-mrr')).toHaveText(/€/)
  // числа докручиваются анимацией: ждём, пока остановятся
  expect(digits(await waitForStableText(page, '[data-testid="mkt-mrr"] [data-role=value]'))).toBe(euros(metric('churn_mrr_eur') * 100))
  expect(digits(await waitForStableText(page, '[data-testid="mkt-arpu"] [data-role=value]'))).toBe(euros(metric('churn_arpu_eur') * 100))
  expect(digits(await waitForStableText(page, '[data-testid="mkt-ltv"] [data-role=value]'))).toBe(euros(metric('churn_ltv_eur') * 100))
  if (m.churn.customer_monthly !== null) {
    const shown = await waitForStableText(page, '[data-testid="mkt-customer-churn"] [data-role=value]')
    expect(shown.replace(/\s/g, '')).toBe(`${percent(metric('churn_customer_churn_rate'))}%`)
  }
  if (m.churn.revenue_monthly !== null) {
    const shown = await waitForStableText(page, '[data-testid="mkt-revenue-churn"] [data-role=value]')
    expect(shown.replace(/\s/g, '')).toBe(`${percent(metric('churn_revenue_churn_rate'))}%`)
  }

  // у каждой карточки есть «i» с объяснением, внизу словарь терминов
  for (const id of ['mkt-mrr', 'mkt-arpu', 'mkt-ltv', 'mkt-customer-churn', 'mkt-revenue-churn', 'mkt-ltv-cac']) {
    await expect(page.getByTestId(id).getByRole('button', { name: /^Что такое/ })).toBeVisible()
  }
  await page.getByTestId('mkt-ltv').getByRole('button', { name: /^Что такое/ }).hover()
  await expect(page.getByRole('tooltip')).toContainText('ARPU × маржа ÷ отток')
  const glossary = page.getByTestId('mkt-glossary')
  for (const term of ['MRR', 'ARPU', 'ARPPU', 'LTV', 'CAC', 'Churn', 'Retention', 'Gross Revenue Churn', 'Payback']) {
    await expect(glossary.getByText(term, { exact: true })).toBeVisible()
  }
  await expect(page.getByTestId('mkt-segments').getByRole('table')).toBeVisible()
  await expect(page.getByTestId('mkt-retention')).toContainText(/удержим|удерживать некого|Не с чем сравнить/)
})

test('админ задаёт CAC и маржу: появляются LTV/CAC и окупаемость', async ({ page, request }) => {
  await loginApi(request)
  const before = await marketing(request)
  saved = { margin: before.settings.margin, cac: before.settings.cac }

  await loginUi(page)
  await page.goto('/marketing')
  const economics = page.getByTestId('mkt-economics')
  await economics.getByLabel('Маржа, %').fill('50')
  await economics.getByLabel(/Стоимость привлечения/).fill('25')
  await economics.getByRole('button', { name: 'Сохранить' }).click()
  await expect(economics.getByRole('status')).toContainText('Сохранено')

  const after = await marketing(request)
  expect(after.settings).toMatchObject({ margin: 0.5, cac: 2500 })
  // LTV линеен по марже; ±1 цент — округление
  expect(Math.abs(after.ltv - before.ltv * (0.5 / before.settings.margin))).toBeLessThanOrEqual(1)
  const ratio = after.unit_economics.ltv_cac
  expect(ratio).toBeCloseTo(after.ltv / 2500, 6)
  await expect(page.getByTestId('mkt-ltv-cac').locator('[data-role=value]')).toHaveText(/×$/)
  await expect(page.getByTestId('mkt-ltv-cac')).toContainText('Окупается за')

  const metrics = await (await request.get('/metrics', { headers: { Authorization: `Bearer ${METRICS_TOKEN}` } })).text()
  expect(Number(metrics.match(/^churn_ltv_eur\s+(\S+)/m)?.[1])).toBeCloseTo(after.ltv / 100, 2)
})

test('аналитик видит страницу, но не меняет CAC', async ({ request }) => {
  await loginApi(request, { username: 'analyst', password: 'analyst' })
  expect((await request.get('/api/v1/analytics/marketing')).status()).toBe(200)
  const put = await request.put('/api/v1/analytics/marketing/settings', { data: { margin: 0.3 }, headers: originHeaders })
  expect(put.status()).toBe(403)
})
