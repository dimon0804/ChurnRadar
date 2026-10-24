import { expect, test, type APIRequestContext } from '@playwright/test'
import { loginApi, loginUi, originHeaders } from './helpers'

// Три роли. Маркетолог пишет клиенту, но не видит модель; аналитик только смотрит, и
// попытку написать письмо отклоняет сервер, а не только интерфейс.
// После спека клиенту возвращается прежний статус, письмо удаляется из Mailpit.
test.describe.configure({ mode: 'serial' })

const MAILPIT = process.env.MAILPIT_URL ?? 'http://localhost:8025'
const CUSTOMER = 5
const MARKETER = {
  username: process.env.MARKETER_USERNAME ?? 'marketer',
  password: process.env.MARKETER_PASSWORD ?? 'marketer',
}
const ANALYST = {
  username: process.env.ANALYST_USERNAME ?? 'analyst',
  password: process.env.ANALYST_PASSWORD ?? 'analyst',
}

type Action = { status: string; until: string | null; note: string | null } | null
let previous: Action = null
let subject = ''

async function actionOf(request: APIRequestContext): Promise<Action> {
  const res = await request.get(`/api/v1/customers/${CUSTOMER}/action`)
  expect(res.status()).toBe(200)
  return ((await res.json()) as { action: Action }).action
}

test.beforeAll(async ({ request }) => {
  await loginApi(request, MARKETER)
  previous = await actionOf(request)
})

test.afterAll(async ({ request, playwright }) => {
  await loginApi(request, MARKETER)
  if (previous === null) {
    await request.delete(`/api/v1/customers/${CUSTOMER}/action`, { headers: originHeaders })
  } else {
    const data: Record<string, unknown> = { status: previous.status }
    if (previous.status === 'snoozed' && previous.until) data.snoozed_until = previous.until
    if (previous.note) data.note = previous.note
    await request.put(`/api/v1/customers/${CUSTOMER}/action`, { data, headers: originHeaders })
  }
  if (subject) {
    const mailpit = await playwright.request.newContext({ baseURL: MAILPIT })
    const list = (await (await mailpit.get('/api/v1/messages', { params: { limit: '50' } })).json()) as {
      messages: { ID: string; Subject: string }[]
    }
    const ids = list.messages.filter((m) => m.Subject === subject).map((m) => m.ID)
    if (ids.length > 0) await mailpit.delete('/api/v1/messages', { data: { IDs: ids } })
    await mailpit.dispose()
  }
})

test('маркетолог пишет клиенту, но не видит модель, данные и пользователей', async ({ page }) => {
  await loginUi(page, MARKETER)
  const nav = page.getByRole('navigation', { name: 'Разделы' })
  await expect(nav.getByRole('link', { name: 'Клиенты' })).toBeVisible()
  await expect(nav.getByRole('link', { name: 'Письма' })).toBeVisible()
  for (const hidden of ['Модель', 'Данные', 'Пользователи']) {
    await expect(nav.getByRole('link', { name: hidden })).toHaveCount(0)
  }

  await page.goto(`/customers/${CUSTOMER}`)
  const panel = page.getByTestId('customer-action')
  await panel.getByRole('button', { name: 'Написать письмо' }).click()
  const dialog = page.getByTestId('email-dialog')
  const field = dialog.getByLabel('Тема')
  await expect(field).not.toHaveValue('')
  subject = `${await field.inputValue()} [roles e2e ${Date.now()}]`
  await field.fill(subject)
  await dialog.getByRole('button', { name: 'Отправить' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(panel.getByTestId('action-badge')).toHaveText(/Письмо отправлено/)

  // прямая ссылка на модель — отказ, и сервер не даст её поменять
  await page.goto('/model')
  await expect(page.getByRole('heading', { name: 'Этот раздел только для администраторов' })).toBeVisible()
  const patch = await page.request.patch('/api/v1/model/settings', { data: {}, headers: originHeaders })
  expect(patch.status()).toBe(403)
  const template = await page.request.put('/api/v1/mail/templates/general', {
    data: { subject: 'x', body: 'y' },
    headers: originHeaders,
  })
  expect(template.status()).toBe(403)
  expect((await page.request.get('/api/v1/mail/templates')).status()).toBe(200)
})

test('аналитик только смотрит: кнопок нет, а сервер отвечает 403', async ({ page }) => {
  await loginUi(page, ANALYST)
  await page.goto(`/customers/${CUSTOMER}`)
  const panel = page.getByTestId('customer-action')
  await expect(panel).toContainText('только просмотр')
  await expect(panel.getByRole('button', { name: 'Написать письмо' })).toHaveCount(0)

  const send = await page.request.post(`/api/v1/customers/${CUSTOMER}/email`, {
    data: { subject: 'Попытка', text: 'Не должно уйти' },
    headers: originHeaders,
  })
  expect(send.status()).toBe(403)
  expect(((await send.json()) as { error: { code: string } }).error.code).toBe('forbidden')

  const mark = await page.request.put(`/api/v1/customers/${CUSTOMER}/action`, {
    data: { status: 'resolved' },
    headers: originHeaders,
  })
  expect(mark.status()).toBe(403)
  const campaign = await page.request.post('/api/v1/mail/campaigns', {
    data: { customer_ids: [CUSTOMER] },
    headers: originHeaders,
  })
  expect(campaign.status()).toBe(403)

  // выгрузка и свои уведомления — можно
  expect((await page.request.get('/api/v1/customers/export.csv')).status()).toBe(200)
  expect((await page.request.get('/api/v1/alerts/preferences')).status()).toBe(200)
})

test('админ выбирает одну из трёх ролей с пояснением', async ({ page }) => {
  await loginUi(page)
  await page.goto('/users')
  await page.getByRole('button', { name: 'Добавить пользователя' }).click()
  const dialog = page.getByRole('dialog', { name: 'Новый пользователь' })
  await expect(dialog.getByTestId('role-hint')).toContainText('Смотрит всё')
  await dialog.getByRole('combobox', { name: 'Роль' }).click()
  await expect(page.getByRole('option')).toHaveText(['Аналитик', 'Маркетолог', 'Администратор'])
  await page.getByRole('option', { name: 'Маркетолог' }).click()
  await expect(dialog.getByTestId('role-hint')).toContainText('Работает с клиентами')
  await dialog.getByRole('button', { name: 'Отмена' }).click()
})
