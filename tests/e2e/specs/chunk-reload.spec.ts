import { expect, test, type Page, type Route } from '@playwright/test'
import { loginApi } from './helpers'

// Открытая вкладка после пересборки панели просит файл страницы, которого уже нет на сервере.
// Панель должна сама перезагрузиться, а не показывать «Unexpected Application Error!».

/** Файл страницы «Модель», но не «Качество модели». */
function isModelChunk(url: string) {
  const name = new URL(url).pathname.split('/').pop() ?? ''
  return /^model-(?!quality)[\w-]+\.js$/.test(name)
}

async function openOverview(page: Page) {
  await loginApi(page.request)
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: 'Обзор' })).toBeVisible()
}

async function goToModel(page: Page) {
  await page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Модель', exact: true }).click()
}

test('удалённый файл страницы: панель перезагружается сама и открывает страницу', async ({ page }) => {
  await openOverview(page)
  let missing = 0
  await page.route('**/assets/*.js', (route: Route) => {
    if (isModelChunk(route.request().url()) && missing === 0) {
      missing += 1
      return route.fulfill({ status: 404, contentType: 'text/plain', body: 'Not found' })
    }
    return route.fallback()
  })

  await goToModel(page)
  await expect(page.getByRole('heading', { level: 1, name: 'Модель риска' })).toBeVisible({ timeout: 20_000 })
  expect(missing).toBe(1)
  await expect(page.getByText('Unexpected Application Error')).toHaveCount(0)
})

test('если перезагрузка не помогла — понятный экран с кнопкой, без цикла', async ({ page }) => {
  await openOverview(page)
  let missing = 0
  await page.route('**/assets/*.js', (route: Route) => {
    if (isModelChunk(route.request().url())) {
      missing += 1
      return route.fulfill({ status: 404, contentType: 'text/plain', body: 'Not found' })
    }
    return route.fallback()
  })

  await goToModel(page)
  await expect(page.getByRole('heading', { name: 'Панель обновилась' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Unexpected Application Error')).toHaveCount(0)
  // одна попытка при переходе и одна после автоматической перезагрузки — дальше не крутимся
  await page.waitForTimeout(1500)
  expect(missing).toBe(2)

  await page.unroute('**/assets/*.js')
  await page.getByRole('button', { name: 'Обновить страницу' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Модель риска' })).toBeVisible({ timeout: 20_000 })
})
