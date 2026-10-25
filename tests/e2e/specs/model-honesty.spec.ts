import { expect, test, type APIRequestContext } from '@playwright/test'
import { loginApi, loginUi, originHeaders } from './helpers'

// Честность модели: калибровка на странице качества, откат и сравнение версий в журнале настроек.
test.describe.configure({ mode: 'serial' })

type Settings = { version: number; horizon_days: number }

async function settings(request: APIRequestContext): Promise<Settings> {
  const res = await request.get('/api/v1/model/settings')
  expect(res.status()).toBe(200)
  return (await res.json()) as Settings
}

async function patch(request: APIRequestContext, body: object): Promise<Settings> {
  const res = await request.patch('/api/v1/model/settings', { data: body, headers: originHeaders })
  expect(res.status()).toBe(200)
  return (await res.json()) as Settings
}

test.beforeAll(async ({ request }) => {
  await loginApi(request)
  await request.post('/api/v1/model/settings/reset', { headers: originHeaders })
})

test.afterAll(async ({ request }) => {
  await loginApi(request)
  await request.post('/api/v1/model/settings/reset', { headers: originHeaders })
})

test('на странице качества есть Brier и reliability-диаграмма', async ({ page }) => {
  await loginUi(page)
  await page.goto('/quality')
  const honesty = page.getByTestId('quality-honesty')
  await expect(honesty).toContainText('Score — это ранжирование, вероятность — калиброванная')

  const brier = page.getByTestId('quality-brier')
  await expect(brier).toContainText('Меньше — лучше')
  await expect(brier.locator('[data-role=calibrated]').first()).toHaveText(/^0,\d{3}$/)

  const reliability = page.getByTestId('quality-reliability')
  await expect(reliability.locator('.recharts-line')).not.toHaveCount(0)
  await expect(reliability).toContainText('Идеальная калибровка')
  await reliability.getByRole('button', { name: 'Таблица' }).click()
  await expect(reliability.getByRole('cell', { name: 'После калибровки' }).first()).toBeVisible()

  await expect(page.getByTestId('quality-challenger')).toContainText('Логистическая регрессия')
  await expect(page.getByTestId('quality-groups')).toContainText('по умолчанию')
})

test('откат к версии из журнала создаёт новую версию', async ({ page, request }) => {
  await loginApi(request)
  const base = await settings(request)
  const first = await patch(request, { horizon_days: 21 })
  const second = await patch(request, { horizon_days: 45 })

  await loginUi(page)
  await page.goto('/model')
  const history = page.getByTestId('settings-history')
  await expect(history.locator(`[data-version="${second.version}"]`)).toContainText('действует')

  await history.getByTestId(`history-rollback-${first.version}`).click()
  const dialog = page.getByRole('alertdialog')
  await expect(dialog).toContainText(`версии ${first.version}`)
  await dialog.getByRole('button', { name: 'Откатить' }).click()

  const next = second.version + 1
  await expect(history.locator(`[data-version="${next}"]`)).toContainText('действует')
  await expect(history.locator(`[data-version="${next}"]`)).toContainText('Горизонт прогноза: 45 → 21 дн.')
  await expect(page.getByRole('textbox', { name: 'Горизонт прогноза, дней' })).toHaveValue('21')

  const after = await settings(request)
  expect(after.version).toBe(next)
  expect(after.horizon_days).toBe(21)
  expect(base.version).toBeLessThan(first.version)
})

test('сравнение двух версий показывает, что поменялось', async ({ page, request }) => {
  await loginApi(request)
  const from = await patch(request, { horizon_days: 14 })
  const to = await patch(request, { horizon_days: 60, thresholds: { high: 70 } })

  await loginUi(page)
  await page.goto('/model')
  const history = page.getByTestId('settings-history')
  await history.getByRole('checkbox', { name: `Сравнить версию ${from.version}` }).check()
  await history.getByRole('checkbox', { name: `Сравнить версию ${to.version}` }).check()

  const result = page.getByTestId('compare-result')
  await expect(result).toContainText(`с v${from.version} до v${to.version}`)
  await expect(result.locator('[data-role=changes]')).toContainText('Горизонт прогноза: 14 → 60 дн.')
  await expect(result.locator('[data-role=changes]')).toContainText('Порог высокого риска: 65 → 70')
  // эффект либо посчитан, либо честно сказано, что сервис оценки недоступен
  await expect(result.locator('[data-role=impact], [data-role=no-impact]')).toBeVisible()
})
