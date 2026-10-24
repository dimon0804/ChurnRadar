import { expect, test } from '@playwright/test'

// Оборванная загрузка файла приложения не оставляет пустую страницу: она перезагружается сама.
test('страница входа переживает обрыв загрузки скрипта', async ({ page }) => {
  let aborted = 0
  await page.route('**/assets/*.js', async (route) => {
    if (aborted === 0) {
      aborted += 1
      return route.abort('connectionreset')
    }
    return route.continue()
  })
  await page.goto('/login')
  await expect(page.getByLabel('Логин')).toBeVisible({ timeout: 15_000 })
  expect(aborted).toBe(1)
})
