import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { loginApi, loginUi, originHeaders } from './helpers'

// Коннекторы-источники: система сама забирает данные из демо-API биллинга и фейковой Метрики.
// Меняют общее состояние (режим симулятора, вкл/выкл коннекторов), поэтому строго по очереди
// и с откатом в конце, чтобы не мешать остальным spec.
test.describe.configure({ mode: 'serial' })

type Status = { last_status: 'ok' | 'error' | 'running' | null; last_records: number | null; run_requested: boolean; last_run_at: string | null }
type Connector = { id: string; enabled: boolean; status: Status }
type Run = { id: number; trigger: string; status: string; records: number }
type Feature = { code: string; title: string; contribution: number }
type Customer = { id: number; features?: Feature[]; reasons?: { code: string }[] }

async function connector(request: APIRequestContext, id: string): Promise<Connector> {
  const res = await request.get(`/api/v1/source/connectors/${id}`)
  expect(res.status()).toBe(200)
  return (await res.json()) as Connector
}

async function setSimulator(request: APIRequestContext, body: Record<string, unknown>) {
  const res = await request.put('/api/v1/simulator', { data: body, headers: originHeaders })
  expect(res.status()).toBe(200)
}

async function patchConnector(request: APIRequestContext, id: string, body: Record<string, unknown>) {
  const res = await request.patch(`/api/v1/source/connectors/${id}`, { data: body, headers: originHeaders })
  expect(res.status()).toBe(200)
}

/** Ждёт, пока запуск, начатый после `after`, закончится успешно. */
async function waitForOkRun(request: APIRequestContext, id: string, after: string | null) {
  await expect
    .poll(
      async () => {
        const c = await connector(request, id)
        const fresh = c.status.last_run_at !== null && c.status.last_run_at !== after
        return fresh && !c.status.run_requested && c.status.last_status !== 'running' ? c.status.last_status : 'pending'
      },
      { timeout: 60_000, intervals: [1000] },
    )
    .toBe('ok')
}

async function runFromUi(page: Page, id: string) {
  const card = page.getByTestId(`connector-${id}`)
  await expect(card).toBeVisible()
  await card.getByRole('button', { name: /^Запустить сейчас/ }).click()
  return card
}

/** Признак модели в карточке клиента (раздел «Из чего сложилась оценка» или «Почему»). */
async function featureContribution(request: APIRequestContext, customerId: number, code: string): Promise<number> {
  const res = await request.get(`/api/v1/customers/${customerId}`)
  expect(res.status()).toBe(200)
  const c = (await res.json()) as Customer
  return c.features?.find((f) => f.code === code)?.contribution ?? 0
}

// у каждого теста свой request-контекст: вход из beforeAll до него не доходит
test.beforeEach(async ({ request }) => {
  await loginApi(request)
})

test.afterAll(async ({ request }) => {
  await loginApi(request)
  await setSimulator(request, { mode: 'push', running: false })
  for (const id of ['billing', 'metrica']) {
    await request.patch(`/api/v1/source/connectors/${id}`, { data: { enabled: false }, headers: originHeaders })
  }
})

test('биллинг в режиме pull: включили коннектор, «Запустить сейчас» → ок и записи в истории', async ({ page, request }) => {
  await setSimulator(request, { mode: 'pull', running: true })
  const before = (await connector(request, 'billing')).status.last_run_at

  await loginUi(page)
  await page.goto('/data')
  const section = page.getByTestId('sources')
  await expect(section.getByRole('heading', { name: 'Источники' })).toBeVisible()

  // переключатель режима симулятора на той же странице
  await expect(page.getByRole('radio', { name: /\(pull\)$/ })).toHaveAttribute('aria-checked', 'true')

  const card = page.getByTestId('connector-billing')
  const toggle = card.getByRole('switch', { name: /^Запускать по расписанию/ })
  if ((await toggle.getAttribute('aria-checked')) !== 'true') {
    const patched = page.waitForResponse((r) => r.url().endsWith('/api/v1/source/connectors/billing') && r.request().method() === 'PATCH')
    await toggle.click()
    expect((await patched).status()).toBe(200)
  }
  await expect(toggle).toHaveAttribute('aria-checked', 'true')

  await runFromUi(page, 'billing')
  await waitForOkRun(request, 'billing', before)

  const status = (await connector(request, 'billing')).status
  expect(status.last_status).toBe('ok')
  expect(status.last_records ?? 0).toBeGreaterThan(0)

  // UI сам дотягивает статус без перезагрузки
  await expect(card.getByTestId('connector-status')).toHaveText('ок', { timeout: 20_000 })
  await expect(card.getByTestId('connector-records')).not.toHaveText(/^(0|—)$/)

  await card.getByRole('button', { name: 'История запусков' }).click()
  const lastRun = card.getByTestId('connector-run').first()
  await expect(lastRun).toHaveAttribute('data-status', 'ok')
  await expect(lastRun).toContainText('вручную')
  const records = Number((await lastRun.getByTestId('connector-run-records').innerText()).replace(/\D/g, ''))
  expect(records).toBeGreaterThan(0)

  const runs = await request.get('/api/v1/source/connectors/billing/runs', { params: { limit: '5' } })
  expect(runs.status()).toBe(200)
  const items = ((await runs.json()) as { items: Run[] }).items
  expect(items[0]?.status).toBe('ok')
  expect(items[0]?.trigger).toBe('manual')
  expect(items[0]?.records ?? 0).toBeGreaterThan(0)

  // откат сразу, не дожидаясь afterAll: следующий тест работает с Метрикой
  await patchConnector(request, 'billing', { enabled: false })
  await setSimulator(request, { mode: 'push', running: false })
})

test('Метрика: «Запустить сейчас» → ок, у клиента 6 видно «Смотрел страницу удаления»', async ({ page, request }) => {
  const before = (await connector(request, 'metrica')).status.last_run_at

  await loginUi(page)
  await page.goto('/data')
  const card = await runFromUi(page, 'metrica')
  await expect(card.getByTestId('metrica-panel')).toBeVisible()
  await waitForOkRun(request, 'metrica', before)
  await expect(card.getByTestId('connector-status')).toHaveText('ок', { timeout: 20_000 })

  // scoring пересчитывает асинхронно: ждём, пока признак появится в оценке
  await expect.poll(() => featureContribution(request, 6, 'cancel_page_views'), { timeout: 60_000, intervals: [1500] }).toBeGreaterThan(0)
  await expect.poll(() => featureContribution(request, 4, 'panel_absence'), { timeout: 30_000, intervals: [1500] }).toBeGreaterThan(0)

  await page.goto('/customers/6')
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('Смотрел страницу удаления').first()).toBeVisible()

  await page.goto('/customers/4')
  await expect(page.getByRole('dialog').getByText('Давно не заходил в панель').first()).toBeVisible()
})
