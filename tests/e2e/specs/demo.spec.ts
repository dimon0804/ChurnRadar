import { expect, test } from '@playwright/test'
import { loginUi } from './helpers'

// Пульт демонстрации: только чтение, состояние стенда не меняет.

test('пульт открывается по Shift+D, но не из поля ввода', async ({ page }) => {
  await loginUi(page)
  await page.goto('/customers')
  const panel = page.getByTestId('demo-panel')

  const search = page.getByLabel('Поиск по email или ID')
  await search.click()
  await page.keyboard.press('Shift+D')
  await expect(search).toHaveValue('D')
  await expect(panel).toHaveCount(0)
  await search.fill('')

  await page.locator('main').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('Shift+D')
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('tab', { name: 'Сценарий' })).toHaveAttribute('aria-selected', 'true')

  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  // после первого открытия в углу остаётся подсказка
  await expect(page.getByTestId('demo-hint')).toBeVisible()
  await page.getByTestId('demo-hint').click()
  await expect(panel).toBeVisible()
})

test('шаг сценария ведёт на нужную страницу и в карточку клиента', async ({ page }) => {
  await loginUi(page)
  await page.goto('/?demo=1')
  const panel = page.getByTestId('demo-panel')
  await expect(panel).toBeVisible()

  await panel.getByRole('button', { name: /^Показать: Таблица клиентов/ }).click()
  await expect(page).toHaveURL(/\/customers\?level=high$/)
  const cue = page.getByTestId('demo-cue')
  await expect(cue).toContainText('Шаг 3 из')
  await expect(cue).toContainText('Что сказать')
  await expect(page.locator('[data-demo-spotlight] > div').first()).toBeVisible()

  await cue.getByRole('button', { name: 'По балансу' }).click()
  await expect(page).toHaveURL(/sort=balance&order=desc/)

  await cue.getByRole('button', { name: /^Дальше/ }).click()
  await expect(page).toHaveURL(/\/customers\/\d+\?level=high$/)
  await expect(page.getByRole('dialog').getByRole('heading', { name: 'Почему' })).toBeVisible()
  await expect(cue).toContainText('Шаг 4 из')
})

test('пульт помнит открытую вкладку', async ({ page }) => {
  await loginUi(page)
  await page.goto('/?demo=1')
  const panel = page.getByTestId('demo-panel')
  await panel.getByRole('tab', { name: 'Что реализовано' }).click()
  await expect(panel.getByText('Сверх кейса')).toBeVisible()

  await page.reload()
  await expect(page.getByTestId('demo-panel').getByRole('tab', { name: 'Что реализовано' })).toHaveAttribute('aria-selected', 'true')
})
