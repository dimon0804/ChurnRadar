import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { ADMIN, baseURL, loginApi, loginUi, originHeaders } from './helpers'

// «Вход и регистрация»: админ выбирает способы входа. Сервер не даёт запереть себя снаружи:
// выключить пароль можно, только войдя через SSO. Часть с Authentik — при SSO_E2E=1
// (docker compose --profile sso up -d).

const SETTINGS = '/api/v1/auth/login-settings'
const PASSWORD_OFF = 'Вход по паролю отключён администратором, войдите через SSO'

async function restorePasswordLogin(request: APIRequestContext) {
  const res = await request.patch(SETTINGS, { data: { password_enabled: true }, headers: originHeaders })
  expect(res.status(), await res.text()).toBe(200)
}

test('страница видна только админу и открывается из меню «Администрирование»', async ({ page, request }) => {
  await loginUi(page)
  const nav = page.getByRole('navigation', { name: 'Разделы' })
  await nav.getByRole('button', { name: /^Администрирование/ }).click()
  await page.getByRole('menuitem', { name: /^Вход и регистрация/ }).click()
  await expect(page).toHaveURL(/\/login-settings$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Вход и регистрация' })).toBeVisible()
  await expect(page.getByRole('switch', { name: 'Логин и пароль' })).toBeChecked()
  await expect(page.getByText('Создавать пользователя при первом входе')).toBeVisible()

  // аналитику и маркетологу менять нельзя — ни через шлюз, ни в самом auth
  for (const user of [
    { username: 'analyst', password: 'analyst' },
    { username: 'marketer', password: 'marketer' },
  ]) {
    await loginApi(request, user)
    const res = await request.patch(SETTINGS, { data: { sso_auto_provision: false }, headers: originHeaders })
    expect(res.status()).toBe(403)
  }
})

test('админ, вошедший паролем, выключить пароль не может: понятная ошибка', async ({ page, request }) => {
  await loginUi(page)
  await page.goto('/login-settings')
  await page.getByRole('switch', { name: 'Логин и пароль' }).click()
  await expect(page.getByRole('alert')).toContainText('войдите в панель через SSO')
  await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toBeDisabled()

  // и сервер отказывает сам, если обойти интерфейс
  await loginApi(request, ADMIN)
  const res = await request.patch(SETTINGS, { data: { password_enabled: false }, headers: originHeaders })
  expect(res.status()).toBe(409)
  const body = await res.json()
  expect(body.error.code).toBe('sso_required')
  expect(body.error.message).toContain('войдите в панель через SSO')

  // выключить всё сразу тоже нельзя
  const all = await request.patch(SETTINGS, {
    data: { password_enabled: false, oidc_enabled: false, saml_enabled: false },
    headers: originHeaders,
  })
  expect(all.status()).toBe(409)
  expect((await all.json()).error.code).toBe('no_login_methods')

  // пароль по-прежнему работает, форма на месте
  expect((await (await request.get('/api/v1/auth/providers')).json()).password).toEqual({ enabled: true })
})

// ---- с Authentik ------------------------------------------------------------------------

async function authentikLogin(page: Page, username: string, password: string) {
  await expect(page).toHaveURL(/\/if\/flow\//, { timeout: 30_000 })
  const login = page.getByRole('textbox', { name: /Имя пользователя|Username/i })
  await login.fill(username)
  await login.press('Enter')
  const secret = page.getByRole('textbox', { name: /Пароль|Password/i })
  await secret.fill(password)
  await secret.press('Enter')
}

test.describe('только SSO', () => {
  test.skip(process.env.SSO_E2E !== '1', 'SSO-профиль не поднят (SSO_E2E=1 включает тесты)')

  test('SSO-админ выключает пароль: /login без формы, вход паролем — 403', async ({ page, browser }) => {
    await page.goto('/login')
    await page.getByRole('link', { name: 'Войти через Authentik (OIDC)' }).click()
    await authentikLogin(page, 'admin', process.env.SSO_ADMIN_PASSWORD ?? 'admin')
    await expect(page).toHaveURL(/localhost:8080\/(?!login)/, { timeout: 30_000 })
    expect((await (await page.request.get('/api/v1/auth/me')).json()).auth_method).toBe('oidc')

    try {
      await page.goto('/login-settings')
      await page.getByRole('switch', { name: 'Логин и пароль' }).click()
      await expect(page.getByText('будут сразу разлогинены')).toBeVisible()
      await page.getByRole('button', { name: 'Сохранить', exact: true }).click()
      const confirm = page.getByRole('alertdialog')
      await expect(confirm).toContainText('Выключить вход по паролю?')
      await confirm.getByRole('button', { name: 'Выключить пароль' }).click()
      await expect(page.getByText('Сохранено.')).toBeVisible()
      await expect(page.getByRole('switch', { name: 'Логин и пароль' })).not.toBeChecked()

      // чужими глазами: страница входа без полей, крупная кнопка SSO
      const guest = await browser.newContext({ baseURL })
      const login = await guest.newPage()
      await login.goto('/login')
      await expect(login.getByTestId('sso-only')).toContainText('Вход по паролю отключён администратором')
      await expect(login.getByLabel('Логин')).toHaveCount(0)
      await expect(login.getByLabel('Пароль', { exact: true })).toHaveCount(0)
      await expect(login.getByRole('link', { name: 'Войти через Authentik (OIDC)' })).toBeVisible()

      // сервер закрыт для пароля, а не только интерфейс
      const res = await guest.request.post('/api/v1/auth/login', { data: ADMIN, headers: originHeaders })
      expect(res.status()).toBe(403)
      expect((await res.json()).error).toMatchObject({ code: 'password_login_disabled', message: PASSWORD_OFF })
      await guest.close()

      // изменение видно в журнале действий
      const audit = await page.request.get('/api/v1/audit', { params: { action: 'auth.settings.update' } })
      expect(audit.status()).toBe(200)
      const items = (await audit.json()).items as { actor: string; success: boolean }[]
      expect(items.some((e) => e.success)).toBe(true)
    } finally {
      // вернуть как было: все способы входа включены
      await restorePasswordLogin(page.request)
    }
    expect((await (await page.request.get('/api/v1/auth/providers')).json()).password).toEqual({ enabled: true })
  })
})
