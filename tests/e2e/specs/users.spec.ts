import { expect, test, type APIRequestContext } from '@playwright/test'
import { ADMIN, loginApi, loginUi, originHeaders } from './helpers'

// Админ заводит аналитика, тот входит, админ его блокирует — войти больше нельзя. В конце удаляем.
const username = `e2e-analyst-${Date.now().toString(36)}`
const password = 'e2e-analyst-pass-1'

async function findUser(request: APIRequestContext) {
  const res = await request.get('/api/v1/auth/users', { params: { q: username } })
  expect(res.status()).toBe(200)
  const body = (await res.json()) as { items: { id: string; username: string; status: string }[] }
  return body.items.find((u) => u.username === username)
}

test.afterAll(async ({ playwright, baseURL }) => {
  const request = await playwright.request.newContext({ baseURL })
  await loginApi(request, ADMIN)
  const user = await findUser(request)
  if (user) await request.delete(`/api/v1/auth/users/${user.id}`, { headers: originHeaders })
  await request.dispose()
})

test('админ создаёт аналитика, блокирует и удаляет его', async ({ browser }) => {
  const adminCtx = await browser.newContext()
  const admin = await adminCtx.newPage()
  await loginUi(admin)
  // служебные разделы администратора — в меню «Администрирование» в шапке
  await admin.getByRole('navigation', { name: 'Разделы' }).getByRole('button', { name: 'Администрирование' }).click()
  await admin.getByRole('menuitem', { name: /^Пользователи/ }).click()
  await expect(admin.getByRole('heading', { name: 'Пользователи', level: 1 })).toBeVisible()

  // создание
  await admin.getByRole('button', { name: 'Добавить пользователя' }).click()
  const dialog = admin.getByRole('dialog', { name: 'Новый пользователь' })
  await dialog.getByLabel('Логин').fill(username)
  await dialog.getByLabel('Имя').fill('Аналитик из e2e')
  await dialog.getByLabel('Email').fill(`${username}@example.com`)
  await dialog.getByRole('radio', { name: 'Задать пароль' }).click()
  await dialog.getByLabel('Пароль', { exact: true }).fill(password)
  await dialog.getByRole('button', { name: 'Создать' }).click()
  await expect(dialog).toBeHidden()
  const row = admin.getByTestId(`user-row-${username}`)
  await expect(row).toContainText('Аналитик')
  await expect(row).toContainText('Активен')

  // аналитик входит и видит панель без админских разделов
  const userCtx = await browser.newContext()
  const user = await userCtx.newPage()
  await loginUi(user, { username, password })
  await expect(user.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Клиенты' })).toBeVisible()
  await expect(user.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Пользователи' })).toHaveCount(0)
  const me = await (await user.request.get('/api/v1/auth/me')).json()
  expect(me).toMatchObject({ username, roles: ['analyst'], email: `${username}@example.com` })

  // блокировка: сессия аналитика сбрасывается сразу, повторный вход отклоняется
  await row.getByRole('button', { name: `Действия: ${username}` }).click()
  await admin.getByRole('menuitem', { name: 'Заблокировать' }).click()
  await expect(row).toContainText('Заблокирован')
  expect((await user.request.get('/api/v1/auth/me')).status()).toBe(401)

  await user.goto('/login')
  await user.getByLabel('Логин').fill(username)
  await user.getByLabel('Пароль', { exact: true }).fill(password)
  await user.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(user.getByRole('alert')).toContainText('заблокирована')
  await expect(user).toHaveURL(/\/login/)

  // журнал видит всё, удаление убирает строку
  await expect(admin.getByText('заблокировал').first()).toBeVisible()
  await row.getByRole('button', { name: `Действия: ${username}` }).click()
  await admin.getByRole('menuitem', { name: 'Удалить' }).click()
  await admin.getByRole('dialog').getByRole('button', { name: 'Удалить' }).click()
  await expect(row).toHaveCount(0)

  await userCtx.close()
  await adminCtx.close()
})

test('пользователь меняет свою почту в профиле', async ({ page }) => {
  await loginUi(page, { username: 'analyst', password: 'analyst' })
  await page.goto('/profile')
  const email = page.getByLabel('Email')
  const before = await email.inputValue()
  await email.fill('analyst-e2e@example.com')
  await page.getByRole('button', { name: 'Сохранить почту' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Сохранено' })).toBeVisible()
  expect((await (await page.request.get('/api/v1/auth/me')).json()).email).toBe('analyst-e2e@example.com')
  // возвращаем как было
  await page.request.patch('/api/v1/auth/me', { data: { email: before || null }, headers: originHeaders })
})
