import { expect, test } from '@playwright/test'
import { loginUi } from './helpers'

// Страница «Качество модели»: только чтение, состояние стенда не меняет.

const ANALYST = { username: 'analyst', password: 'analyst' }

test('без входа результаты проверки не отдаются', async ({ request }) => {
  const res = await request.get('/api/v1/model/quality')
  expect(res.status()).toBe(401)
})

test('аналитик открывает «Качество модели» из меню и видит AUC и lift', async ({ page }) => {
  await loginUi(page, ANALYST)
  await page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Качество модели' }).click()
  await expect(page).toHaveURL(/\/quality$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Качество модели' })).toBeVisible()

  await expect(page.getByTestId('quality-auc').locator('[data-role=value]')).toHaveText(/^0,\d\d(–0,\d\d)?$/)
  await expect(page.getByTestId('quality-lift').locator('[data-role=value]')).toHaveText(/^в \d+,\d(–\d+,\d)? раза$/)

  // графики нарисованы, у каждого есть таблица
  const lift = page.getByTestId('quality-lift-chart')
  await expect(lift.locator('.recharts-bar-rectangle')).toHaveCount(3)
  await lift.getByRole('button', { name: 'Таблица' }).click()
  await expect(lift.getByRole('cell', { name: 'Высокий риск' })).toBeVisible()
  await expect(page.getByTestId('quality-roc').locator('.recharts-line')).not.toHaveCount(0)

  await expect(page.getByTestId('quality-baselines')).toContainText('Правило «ждёт оплаты»')
  await expect(page.getByTestId('quality-method')).toContainText('Ограничения')
})

test('переключение сценария меняет графики, но не заголовок', async ({ page }) => {
  await loginUi(page)
  await page.goto('/quality')
  const auc = page.getByTestId('quality-auc').locator('[data-role=value]')
  const before = await auc.textContent()
  const baselines = page.getByTestId('quality-baselines')
  const streamTable = await baselines.locator('tbody').textContent()

  await page.getByRole('button', { name: 'Продления по сроку' }).click()
  await expect(page.getByRole('button', { name: 'Продления по сроку' })).toHaveAttribute('aria-pressed', 'true')
  await expect(baselines.locator('tbody')).not.toHaveText(streamTable ?? '')
  await expect(auc).toHaveText(before ?? '')
})
