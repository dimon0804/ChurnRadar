import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { loginUi } from './helpers'

// Админ выпускает API-токен в панели, внешняя система ходит с ним в API без cookie,
// после отзыва — 401. Создание токена видно в журнале действий. Отчёт скачивается PDF.
const tokenName = `e2e-aeza-${Date.now().toString(36)}`

test('API-токен: выпуск, доступ по Bearer, отзыв, журнал и отчёт', async ({ page, playwright, baseURL }) => {
  await loginUi(page)
  await page.goto('/integrations')
  await expect(page.getByRole('heading', { name: 'Интеграции', level: 1 })).toBeVisible()

  await page.getByRole('button', { name: 'Новый токен' }).first().click()
  const dialog = page.getByRole('dialog', { name: 'Новый API-токен' })
  await dialog.getByLabel('Имя').fill(tokenName)
  await dialog.getByRole('button', { name: 'Выпустить токен' }).click()

  const secretBox = page.getByTestId('token-secret')
  await expect(secretBox).toBeVisible()
  const secret = (await secretBox.innerText()).trim()
  expect(secret).toMatch(/^cr_/)
  await page.getByRole('button', { name: 'Я сохранил токен' }).click()

  // внешняя система: отдельный контекст, никакой сессии — только заголовок
  const machine = await playwright.request.newContext({ baseURL })
  const auth = { Authorization: `Bearer ${secret}` }
  const ok = await machine.get('/api/v1/customers', { headers: auth, params: { page_size: '5' } })
  expect(ok.status()).toBe(200)
  expect(((await ok.json()) as { items: unknown[] }).items.length).toBeGreaterThan(0)

  const row = page.getByTestId(`token-row-${tokenName}`)
  await row.getByRole('button', { name: 'Отозвать' }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: 'Отозвать токен' }).click()
  await expect(row).toContainText(/отозван/i)

  const denied = await machine.get('/api/v1/customers', { headers: auth })
  expect(denied.status()).toBe(401)
  await machine.dispose()

  // журнал: запись о создании токена с его именем
  await page.goto(`/audit?q=${encodeURIComponent(tokenName)}`)
  await expect(page.getByRole('heading', { name: 'Журнал действий', level: 1 })).toBeVisible()
  await expect(page.getByTestId('audit-row').filter({ hasText: 'Создан API-токен' }).filter({ hasText: tokenName })).toBeVisible()

  // отчёт для руководства
  await page.goto('/')
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', { name: 'Скачать отчёт' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toMatch(/\.pdf$/)
  const file = await download.path()
  expect(readFileSync(file).subarray(0, 5).toString()).toBe('%PDF-')
})
