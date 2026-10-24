import { expect, test, type APIRequestContext } from '@playwright/test'
import { loginApi, loginUi, originHeaders } from './helpers'

// Письмо клиенту из карточки: шаблон по причине риска → отправка через SMTP → письмо в Mailpit.
// После проверки возвращаем клиенту прежний статус и удаляем письмо из Mailpit.
test.describe.configure({ mode: 'serial' })

const MAILPIT = process.env.MAILPIT_URL ?? 'http://localhost:8025'
const CUSTOMER = 9
const ANALYST = {
  username: process.env.ANALYST_USERNAME ?? 'analyst',
  password: process.env.ANALYST_PASSWORD ?? 'analyst',
}

type Action = { status: 'emailed' | 'resolved' | 'snoozed'; until: string | null; note: string | null } | null
type MailpitMessage = { ID: string; Subject: string; To: { Address: string }[]; ReplyTo: { Address: string }[]; Created: string }

let previous: Action = null
let sentSubject = ''
const mailpitIds: string[] = []

async function actionOf(request: APIRequestContext): Promise<Action> {
  const res = await request.get(`/api/v1/customers/${CUSTOMER}/action`)
  expect(res.status()).toBe(200)
  return ((await res.json()) as { action: Action }).action
}

test.beforeAll(async ({ request }) => {
  await loginApi(request, ANALYST)
  previous = await actionOf(request)
})

test.afterAll(async ({ request, playwright }) => {
  await loginApi(request, ANALYST)
  if (previous === null) {
    await request.delete(`/api/v1/customers/${CUSTOMER}/action`, { headers: originHeaders })
  } else {
    const data: Record<string, unknown> = { status: previous.status }
    if (previous.status === 'snoozed' && previous.until) data.snoozed_until = previous.until
    if (previous.note) data.note = previous.note
    await request.put(`/api/v1/customers/${CUSTOMER}/action`, { data, headers: originHeaders })
  }
  if (mailpitIds.length > 0) {
    const mailpit = await playwright.request.newContext({ baseURL: MAILPIT })
    await mailpit.delete('/api/v1/messages', { data: { IDs: mailpitIds } })
    await mailpit.dispose()
  }
})

test('черновик берёт шаблон по главной причине и подставляет данные клиента', async ({ request }) => {
  await loginApi(request, ANALYST)
  const res = await request.post(`/api/v1/customers/${CUSTOMER}/email/draft`, { data: {}, headers: originHeaders })
  expect(res.status()).toBe(200)
  const draft = (await res.json()) as { to: string; template_code: string; subject: string; text: string; enabled: boolean }
  expect(draft.to).toBe('user-0009@example.test')
  expect(draft.enabled).toBe(true)
  // у клиента 9 в данных кейса главная причина — не хватит денег на продление
  expect(draft.template_code).toBe('insufficient_funds')
  expect(draft.subject).toMatch(/^Пополните баланс до /)
  expect(draft.text).toContain('€915,06')
  expect(draft.text).not.toContain('{{')
  expect(`${draft.subject} ${draft.text}`).not.toMatch(/звон/i)
})

test('письмо из карточки уходит в Mailpit, клиент получает статус «Письмо отправлено»', async ({ page, playwright }) => {
  const startedAt = Date.now()
  await loginUi(page, ANALYST)
  await page.goto(`/customers/${CUSTOMER}`)
  const panel = page.getByTestId('customer-action')
  await panel.getByRole('button', { name: 'Написать письмо' }).click()

  const dialog = page.getByTestId('email-dialog')
  await expect(dialog.getByTestId('email-to')).toHaveText('user-0009@example.test')
  const subject = dialog.getByLabel('Тема')
  await expect(subject).toHaveValue(/^Пополните баланс до /)
  sentSubject = `${await subject.inputValue()} [e2e ${startedAt}]`
  await subject.fill(sentSubject)
  await dialog.getByRole('button', { name: 'Как увидит клиент' }).click()
  await expect(dialog.getByTestId('letter-preview')).toContainText(sentSubject)
  await dialog.getByRole('button', { name: 'Отправить' }).click()
  await expect(dialog).toHaveCount(0)

  await expect(panel.getByTestId('action-badge')).toHaveText(/Письмо отправлено/)
  await expect(panel.getByRole('list', { name: 'Журнал' })).toContainText(`Письмо отправлено: «${sentSubject}»`)

  const mailpit = await playwright.request.newContext({ baseURL: MAILPIT })
  try {
    let found: MailpitMessage | undefined
    await expect
      .poll(async () => {
        const res = await mailpit.get('/api/v1/messages', { params: { limit: '50' } })
        const body = (await res.json()) as { messages: MailpitMessage[] }
        found = body.messages.find((m) => m.Subject === sentSubject)
        return Boolean(found)
      })
      .toBe(true)
    mailpitIds.push(found!.ID)
    expect(found!.To.map((t) => t.Address)).toEqual(['user-0009@example.test'])
    expect(found!.ReplyTo.length).toBe(1)
    const full = await mailpit.get(`/api/v1/message/${found!.ID}`)
    const message = (await full.json()) as { Text: string; HTML: string }
    expect(message.Text).toContain('€915,06')
    expect(message.HTML).toContain('<p>')
  } finally {
    await mailpit.dispose()
  }
})

test('клиент с письмом ушёл из очереди и попадает под защиту от повторов', async ({ request }) => {
  await loginApi(request, ANALYST)
  const queue = await request.get('/api/v1/contact-today', { params: { limit: '100' } })
  const ids = ((await queue.json()) as { items: { id: number }[] }).items.map((i) => i.id)
  expect(ids).not.toContain(CUSTOMER)

  const preview = await request.post('/api/v1/mail/campaigns/preview', {
    data: { customer_ids: [CUSTOMER], skip_days: 7 },
    headers: originHeaders,
  })
  expect(preview.status()).toBe(200)
  const body = (await preview.json()) as { to_send: number; skipped_recent: number }
  expect(body).toMatchObject({ to_send: 0, skipped_recent: 1 })

  const stats = await request.get('/api/v1/mail/stats')
  expect(((await stats.json()) as { sent: number }).sent).toBeGreaterThan(0)
})

test('рассылка выбранным: предпросмотр количества и примера, без отправки', async ({ page }) => {
  await loginUi(page, ANALYST)
  await page.goto('/customers?level=high&sort=expected_loss&order=desc')
  await expect(page.getByRole('checkbox', { name: /^Выбрать клиента/ })).toHaveCount(4)
  await page.getByRole('checkbox', { name: 'Выбрать всех на странице' }).check()
  const bar = page.getByTestId('selection-bar')
  await expect(bar).toContainText('Выбрано: 4')
  await bar.getByRole('button', { name: 'Написать выбранным' }).click()
  const dialog = page.getByTestId('campaign-dialog')
  // клиенту 9 только что писали — его пропустим
  await expect(dialog.getByTestId('campaign-summary')).toContainText('Уйдёт 3')
  await expect(dialog.getByTestId('campaign-summary')).toContainText('1 клиента пропустим')
  await expect(dialog.getByTestId('letter-preview')).toBeVisible()
  await dialog.getByRole('button', { name: 'Отмена' }).click()
  await expect(dialog).toHaveCount(0)
})
