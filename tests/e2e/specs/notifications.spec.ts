import { expect, test, type APIRequestContext } from '@playwright/test'
import { loginApi, loginUi, originHeaders } from './helpers'

// Персональные уведомления: порог в настройках → событие в базе → письмо в Mailpit и запись под колокольчиком.
test.describe.configure({ mode: 'serial' })

const MAILPIT = process.env.MAILPIT_URL ?? 'http://localhost:8025'
const EMAIL = `e2e-notify-${Date.now()}@example.com`
const BIG_CUSTOMER = 'Крупный клиент перешёл в высокий риск'

type MailpitMessage = { ID: string; Subject: string; To: { Address: string }[] }

async function mailsTo(request: APIRequestContext, address: string): Promise<MailpitMessage[]> {
  const res = await request.get(`${MAILPIT}/api/v1/messages`, { params: { limit: '200' } })
  expect(res.status()).toBe(200)
  const body = (await res.json()) as { messages: MailpitMessage[] }
  return body.messages.filter((m) => m.To.some((t) => t.Address === address))
}

async function restore(request: APIRequestContext) {
  await loginApi(request)
  await request.post('/api/v1/model/settings/reset', { headers: originHeaders })
  // свои настройки уведомлений — к значениям по умолчанию (admin может сбросить и свои)
  await request.delete('/api/v1/alerts/preferences/users/admin', { headers: originHeaders })
  const ids = (await mailsTo(request, EMAIL)).map((m) => m.ID)
  if (ids.length) await request.delete(`${MAILPIT}/api/v1/messages`, { data: { IDs: ids } })
}

test.beforeAll(async ({ request }) => {
  await restore(request)
})

test.afterAll(async ({ request }) => {
  await restore(request)
})

test('порог «крупный клиент» → правка модели → письмо в Mailpit и уведомление под колокольчиком', async ({ page }) => {
  await loginUi(page)
  await page.goto('/notifications')
  await expect(page.getByRole('heading', { name: 'Уведомления' })).toBeVisible()

  // настраиваем: почта, крупный клиент от €1, каналы «в панели» и «почта»
  await page.getByLabel('Почта', { exact: true }).fill(EMAIL)
  const row = page.getByTestId('event-big_customer_high')
  const toggle = row.getByRole('switch', { name: BIG_CUSTOMER })
  if ((await toggle.getAttribute('aria-checked')) !== 'true') await toggle.click()
  await row.getByRole('spinbutton', { name: `Порог: ${BIG_CUSTOMER}` }).fill('1')
  const channels = row.getByRole('group', { name: `Каналы: ${BIG_CUSTOMER}` })
  const mail = channels.getByRole('button', { name: 'Почта' })
  if ((await mail.getAttribute('aria-pressed')) !== 'true') await mail.click()
  await expect(row.getByText(/Пример: «Крупный клиент #1042/)).toBeVisible()

  const saved = page.waitForResponse((r) => r.url().endsWith('/api/v1/alerts/preferences') && r.request().method() === 'PUT')
  await page.getByTestId('save-preferences').click()
  expect((await saved).status()).toBe(200)

  // тестовое уведомление доходит до почтового ящика
  await page.getByRole('button', { name: 'Отправить тестовое уведомление' }).click()
  await expect(page.getByTestId('test-results')).toContainText(/Почта:\s*отправлено/)
  await expect
    .poll(async () => (await mailsTo(page.request, EMAIL)).map((m) => m.Subject), { timeout: 30_000 })
    .toContain('ChurnRadar: Тестовое уведомление')

  // событие: порог высокого риска опускаем, и в высокий риск разом уходят клиенты с ожидаемой потерей
  const patch = await page.request.patch('/api/v1/model/settings', {
    data: { thresholds: { medium: 5, high: 10 } },
    headers: originHeaders,
  })
  expect(patch.status()).toBe(200)

  await expect
    .poll(async () => (await mailsTo(page.request, EMAIL)).map((m) => m.Subject).join('\n'), {
      timeout: 90_000,
      intervals: [2_000],
    })
    .toMatch(/в высокий риск/)

  // то же событие — в личной ленте под колокольчиком, со ссылкой в панель
  await page.reload()
  await page.getByTestId('alerts-bell').click()
  const inbox = page.getByTestId('alerts-inbox')
  await expect(inbox).toContainText('в высокий риск')
  await expect(inbox.getByRole('link').first()).toHaveAttribute('href', /\/customers/)
})
