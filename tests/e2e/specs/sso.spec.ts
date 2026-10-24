import { expect, test, type Page } from '@playwright/test'
import { ADMIN, loginApi, originHeaders } from './helpers'

// Вход через Authentik по OIDC и SAML. Нужен профиль sso: docker compose --profile sso up -d,
// поэтому по умолчанию тесты пропускаются; включаются переменной SSO_E2E=1.
test.skip(process.env.SSO_E2E !== '1', 'SSO-профиль не поднят (SSO_E2E=1 включает тесты)')

const SSO_USERS = {
  marketer: process.env.SSO_MARKETER_PASSWORD ?? 'marketer',
  admin: process.env.SSO_ADMIN_PASSWORD ?? 'admin',
  outsider: process.env.SSO_OUTSIDER_PASSWORD ?? 'outsider',
}

/** Форма входа Authentik: сначала логин, потом пароль (два шага одного flow). */
async function authentikLogin(page: Page, username: keyof typeof SSO_USERS) {
  await expect(page).toHaveURL(/\/if\/flow\//, { timeout: 30_000 })
  const login = page.getByRole('textbox', { name: /Имя пользователя|Username/i })
  await login.fill(username)
  await login.press('Enter')
  const password = page.getByRole('textbox', { name: /Пароль|Password/i })
  await password.fill(SSO_USERS[username])
  await password.press('Enter')
}

async function ssoLogin(page: Page, method: 'OIDC' | 'SAML', username: keyof typeof SSO_USERS) {
  await page.goto('/login')
  await page.getByRole('link', { name: `Войти через Authentik (${method})` }).click()
  await authentikLogin(page, username)
}

test('кнопки обоих способов видны на странице входа', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('link', { name: 'Войти через Authentik (OIDC)' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Войти через Authentik (SAML)' })).toBeVisible()
})

test('OIDC: маркетолог входит аналитиком', async ({ page }) => {
  await ssoLogin(page, 'OIDC', 'marketer')
  await expect(page).toHaveURL(/localhost:8080\/(?!login)/, { timeout: 30_000 })
  const me = await (await page.request.get('/api/v1/auth/me')).json()
  expect(me).toMatchObject({ username: 'marketer', roles: ['analyst'], auth_method: 'oidc' })
  // роль применяется: админских разделов нет, менять модель сервер не даёт
  await expect(page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Клиенты' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Модель' })).toHaveCount(0)
  const patch = await page.request.patch('/api/v1/model/settings', { data: {}, headers: originHeaders })
  expect(patch.status()).toBe(403)
})

test('SAML: admin из группы churn-admins входит администратором', async ({ page }) => {
  await ssoLogin(page, 'SAML', 'admin')
  await expect(page).toHaveURL(/localhost:8080\/(?!login)/, { timeout: 30_000 })
  const me = await (await page.request.get('/api/v1/auth/me')).json()
  expect(me).toMatchObject({ roles: ['admin'], auth_method: 'saml' })
  await expect(page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Модель' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Пользователи' })).toBeVisible()
})

test('пользователь без группы ChurnRadar получает отказ', async ({ page }) => {
  await ssoLogin(page, 'SAML', 'outsider')
  await expect(page).toHaveURL(/\/login\?error=forbidden/, { timeout: 30_000 })
  await expect(page.getByRole('alert')).toContainText('нет доступа')
})

test('заблокированный в панели не входит и через SSO', async ({ browser, request }) => {
  // входим маркетологом, чтобы узнать id его учётки в панели, и блокируем её админом
  const first = await browser.newPage()
  await ssoLogin(first, 'OIDC', 'marketer')
  await expect(first).toHaveURL(/localhost:8080\/(?!login)/, { timeout: 30_000 })
  const { id } = await (await first.request.get('/api/v1/auth/me')).json()
  await loginApi(request, ADMIN)
  const block = await request.patch(`/api/v1/auth/users/${id}`, { data: { status: 'blocked' }, headers: originHeaders })
  expect(block.status()).toBe(200)
  try {
    // открытая сессия сброшена сразу
    expect((await first.request.get('/api/v1/auth/me')).status()).toBe(401)
    const page = await browser.newPage()
    await ssoLogin(page, 'OIDC', 'marketer')
    await expect(page).toHaveURL(/\/login\?error=blocked/, { timeout: 30_000 })
    await expect(page.getByRole('alert')).toContainText('заблокирована')
  } finally {
    await request.patch(`/api/v1/auth/users/${id}`, { data: { status: 'active' }, headers: originHeaders })
    await first.close()
  }
})
