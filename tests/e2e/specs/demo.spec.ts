import { expect, test, type Locator } from '@playwright/test'
import { loginUi } from './helpers'

// Пульт демонстрации: только чтение, состояние стенда не меняет.

/** Номер шага по списку в пульте: шаги добавляются, поэтому ни номер, ни общее число не зашиваем. */
async function stepLabel(panel: Locator, title: string, offset = 0) {
  const labels = await panel
    .locator('ol')
    .first()
    .getByRole('button', { name: /^Показать: / })
    .evaluateAll((els) => els.map((el) => el.getAttribute('aria-label') ?? ''))
  const index = labels.findIndex((l) => l === `Показать: ${title}`)
  expect(index, `шаг «${title}» есть в сценарии`).toBeGreaterThanOrEqual(0)
  return `Шаг ${index + 1 + offset} из ${labels.length}`
}

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
  const tableStep = await stepLabel(panel, 'Таблица клиентов: фильтр и сортировка')
  const cardStep = await stepLabel(panel, 'Таблица клиентов: фильтр и сортировка', 1)

  await panel.getByRole('button', { name: /^Показать: Таблица клиентов/ }).click()
  await expect(page).toHaveURL(/\/customers\?level=high$/)
  const cue = page.getByTestId('demo-cue')
  await expect(cue).toContainText(tableStep)
  await expect(cue).toContainText('Что сказать')
  await expect(page.locator('[data-demo-spotlight] > div').first()).toBeVisible()

  await cue.getByRole('button', { name: 'По балансу' }).click()
  await expect(page).toHaveURL(/sort=balance&order=desc/)

  await cue.getByRole('button', { name: /^Дальше/ }).click()
  await expect(page).toHaveURL(/\/customers\/\d+\?level=high$/)
  await expect(page.getByRole('dialog').getByRole('heading', { name: 'Почему' })).toBeVisible()
  await expect(cue).toContainText(cardStep)
})

test('шаги «С кем связаться» и «Качество модели» подсвечивают нужные блоки', async ({ page }) => {
  await loginUi(page)
  await page.goto('/customers?demo=1')
  const panel = page.getByTestId('demo-panel')
  const spotlight = page.locator('[data-demo-spotlight] > div').first()
  await expect(panel).toBeVisible()
  const contactStep = await stepLabel(panel, 'С кем связаться сегодня')
  const qualityStep = await stepLabel(panel, 'Качество модели: проверяем, что она не гадает')

  await panel.getByRole('button', { name: /^Показать: С кем связаться сегодня/ }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByTestId('contact-today')).toBeVisible()
  await expect(page.getByTestId('contact-today-mass')).toBeVisible()
  const cue = page.getByTestId('demo-cue')
  await expect(cue).toContainText(contactStep)
  await expect(spotlight).toBeVisible()

  // после шага открыта подсказка, а не пульт: открываем пульт заново
  await page.goto('/?demo=1')
  await page.getByTestId('demo-panel').getByRole('button', { name: /^Показать: Качество модели/ }).click()
  await expect(page).toHaveURL(/\/quality$/)
  await expect(page.getByTestId('quality-lift')).toBeVisible()
  await expect(page.getByTestId('quality-auc')).toBeVisible()
  await expect(page.getByTestId('demo-cue')).toContainText(qualityStep)
  await expect(spotlight).toBeVisible()
})

test('шаг модели включает «Что если» и ничего не сохраняет', async ({ page }) => {
  await loginUi(page)
  await page.goto('/?demo=1')
  await page.getByTestId('demo-panel').getByRole('button', { name: /^Показать: Модель/ }).click()
  await expect(page).toHaveURL(/\/model$/)
  const whatIfSwitch = page.locator('label', { hasText: 'Что если' }).getByRole('switch')
  await expect(whatIfSwitch).toHaveAttribute('aria-checked', 'true')
  const whatIf = page.getByTestId('what-if')
  await expect(whatIf).toBeVisible()
  await expect(page.getByTestId('demo-cue')).toContainText('«Что если» включён')

  // предпросмотр живёт только в браузере: «Отменить» возвращает страницу как была
  await whatIf.getByRole('button', { name: 'Отменить' }).click()
  await expect(whatIfSwitch).toHaveAttribute('aria-checked', 'false')
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
