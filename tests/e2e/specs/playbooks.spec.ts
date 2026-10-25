import { expect, test, type APIRequestContext } from '@playwright/test'
import { loginApi, loginUi, originHeaders } from './helpers'

// Сценарий удержания: создаём в режиме симуляции → видно, сколько клиентов попадает под правило;
// включаем боевой режим и запускаем → письмо приходит в Mailpit. В конце выключаем и удаляем
// сценарий, возвращаем клиенту прежний статус и убираем письмо из Mailpit.
test.describe.configure({ mode: 'serial' })

const MAILPIT = process.env.MAILPIT_URL ?? 'http://localhost:8025'
const NAME = `e2e: высокий риск ${Date.now()}`

type Action = { status: 'emailed' | 'resolved' | 'snoozed'; until: string | null; note: string | null } | null
type MailpitMessage = { ID: string; Subject: string; To: { Address: string }[]; Created: string }
type Sample = { customer_id: number; email: string; group: 'treated' | 'control' }
type PlaybookItem = { id: number; name: string; enabled: boolean; mode: string }

let playbookId: number | null = null
let target: Sample | null = null
let previous: Action = null
const mailpitIds: string[] = []

const RULE = {
  name: NAME,
  conditions: { risk_levels: ['high'] },
  actions: [{ type: 'email', template_code: 'general' }],
  control_share: 0,
  max_per_day: 1,
  cooldown_days: 14,
  mode: 'simulation',
  enabled: true,
}

async function findPlaybook(request: APIRequestContext): Promise<PlaybookItem | undefined> {
  const res = await request.get('/api/v1/playbooks')
  expect(res.status()).toBe(200)
  return ((await res.json()) as { items: PlaybookItem[] }).items.find((p) => p.name === NAME)
}

async function actionOf(request: APIRequestContext, id: number): Promise<Action> {
  const res = await request.get(`/api/v1/customers/${id}/action`)
  return ((await res.json()) as { action: Action }).action
}

/**
 * Ждёт, пока пересчёт догонит текущую версию настроек модели. Спек перед нами (уведомления)
 * в конце сбрасывает настройки, и пересчёт идёт в фоне: без ожидания сервер и диалог считают
 * правило на разных стадиях пересчёта (6 и 5 клиентов высокого риска).
 */
async function waitForScoring(request: APIRequestContext) {
  const settings = (await (await request.get('/api/v1/model/settings')).json()) as { version: number }
  await expect
    .poll(
      async () => ((await (await request.get('/api/v1/analytics/summary')).json()) as { model_version: number }).model_version,
      { timeout: 60_000, intervals: [1000] },
    )
    .toBeGreaterThanOrEqual(settings.version)
}

test.afterAll(async ({ request, playwright }) => {
  await loginApi(request)
  const left = await findPlaybook(request)
  if (left) await request.delete(`/api/v1/playbooks/${left.id}`, { headers: originHeaders })
  if (target) {
    if (previous === null) {
      await request.delete(`/api/v1/customers/${target.customer_id}/action`, { headers: originHeaders })
    } else {
      const data: Record<string, unknown> = { status: previous.status }
      if (previous.status === 'snoozed' && previous.until) data.snoozed_until = previous.until
      if (previous.note) data.note = previous.note
      await request.put(`/api/v1/customers/${target.customer_id}/action`, { data, headers: originHeaders })
    }
  }
  if (mailpitIds.length > 0) {
    const mailpit = await playwright.request.newContext({ baseURL: MAILPIT })
    await mailpit.delete('/api/v1/messages', { data: { IDs: mailpitIds } })
    await mailpit.dispose()
  }
})

test('сценарий в режиме симуляции: видно, сколько клиентов попадает под правило', async ({ page, request }) => {
  await loginApi(request)
  await waitForScoring(request)
  const res = await request.post('/api/v1/playbooks/preview', { data: RULE, headers: originHeaders })
  expect(res.status()).toBe(200)
  const preview = (await res.json()) as { matched: number; treated: number; control: number; sample: Sample[] }
  expect(preview.matched).toBeGreaterThan(0)
  expect(preview.control).toBe(0)
  target = preview.sample[0] ?? null
  expect(target?.group).toBe('treated')
  previous = await actionOf(request, target!.customer_id)

  await loginUi(page)
  await page.goto('/playbooks')
  await expect(page.getByTestId('playbook-presets')).toBeVisible()
  await page.getByRole('button', { name: 'Новый сценарий' }).click()
  const dialog = page.getByRole('dialog', { name: 'Новый сценарий' })
  await dialog.getByLabel('Название').fill(NAME)
  await dialog.getByRole('group', { name: 'Уровень риска' }).getByRole('button', { name: 'Высокий' }).click()
  await dialog.getByRole('combobox', { name: 'Шаблон письма' }).click()
  await page.getByRole('option', { name: 'Общий' }).click()
  await dialog.getByRole('combobox', { name: 'Контрольная группа' }).click()
  await page.getByRole('option', { name: 'без контроля' }).click()
  await dialog.getByLabel('Клиентов в день, не больше').fill('1')

  // предпросмотр совпадает с тем, что посчитал сервер для того же правила
  await expect(dialog.getByTestId('preview-matched')).toHaveText(String(preview.matched))
  await expect(dialog.getByTestId('preview-control')).toHaveText('0')
  await expect(dialog.getByTestId('preview-treated')).toHaveText('1')
  await dialog.getByRole('button', { name: 'Сохранить' }).click()
  await expect(dialog).toHaveCount(0)

  const detail = page.getByTestId('playbook-detail')
  await expect(detail).toContainText(NAME)
  await expect(detail).toContainText('Симуляция')
  await expect(page.getByTestId('playbook-effect')).toBeVisible()
  const saved = await findPlaybook(request)
  expect(saved).toMatchObject({ mode: 'simulation', enabled: true })
  playbookId = saved!.id

  // в симуляции «Запустить сейчас» ничего не отправляет
  await detail.getByRole('button', { name: 'Запустить сейчас' }).click()
  await expect(detail.getByTestId('playbook-run-note')).toContainText('Ничего не отправлено')
})

test('боевой режим: письмо уходит в Mailpit, затем сценарий выключаем и удаляем', async ({ page, playwright, request }) => {
  test.skip(playbookId === null || target === null, 'сценарий не создан')
  const startedAt = Date.now() - 5_000
  await loginUi(page)
  await page.goto(`/playbooks?id=${playbookId}`)
  const detail = page.getByTestId('playbook-detail')
  await detail.getByRole('switch', { name: 'Боевой режим' }).click()
  await expect(detail).toContainText('Работает')
  await detail.getByRole('button', { name: 'Запустить сейчас' }).click()
  // дневной лимит мог уже израсходовать пересчёт, сработавший сразу после включения, — письмо всё равно одно
  await expect(detail.getByTestId('playbook-run-note')).toContainText('Готово')

  const mailpit = await playwright.request.newContext({ baseURL: MAILPIT })
  try {
    let found: MailpitMessage | undefined
    await expect
      .poll(async () => {
        const res = await mailpit.get('/api/v1/messages', { params: { limit: '50' } })
        const body = (await res.json()) as { messages: MailpitMessage[] }
        found = body.messages.find(
          (m) => m.To.some((t) => t.Address === target!.email) && Date.parse(m.Created) >= startedAt && m.Subject.startsWith('Нужна помощь с'),
        )
        return Boolean(found)
      })
      .toBe(true)
    mailpitIds.push(found!.ID)
  } finally {
    await mailpit.dispose()
  }

  await loginApi(request)
  const hits = await request.get(`/api/v1/playbooks/${playbookId}/hits`)
  const items = ((await hits.json()) as { items: { customer_id: number; group: string }[] }).items
  expect(items).toEqual([expect.objectContaining({ customer_id: target!.customer_id, group: 'treated' })])

  await page.getByRole('tab', { name: 'Клиенты' }).click()
  await expect(page.getByRole('table', { name: 'Срабатывания сценария' })).toContainText(target!.email)

  await detail.getByRole('switch', { name: 'Сценарий включён' }).click()
  await expect(detail).toContainText('Выключен')
  expect((await findPlaybook(request))?.enabled).toBe(false)

  await detail.getByRole('button', { name: 'Удалить сценарий' }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: 'Удалить' }).click()
  await expect(page.getByText(NAME)).toHaveCount(0)
  expect(await findPlaybook(request)).toBeUndefined()
})
