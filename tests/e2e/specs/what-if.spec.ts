import { expect, test, type Page } from '@playwright/test'
import { loginApi, loginUi, originHeaders } from './helpers'

// «Что если» на странице модели: правки видны до сохранения и не сохраняются без «Применить».
test.describe.configure({ mode: 'serial' })

const FEATURE = 'prolong_wait'
const TITLE = 'Ждёт оплаты'

type Weight = { code: string; weight: number; title: string }
type Settings = { version: number; weights: Weight[] }
type Totals = { expected_loss: number; by_level: Record<string, number>; revenue_at_risk_share: number }
type Preview = { exact: boolean; model_version: number; before: Totals; after: Totals }

async function settings(page: Page): Promise<Settings> {
  const res = await page.request.get('/api/v1/model/settings')
  expect(res.status()).toBe(200)
  return (await res.json()) as Settings
}

const weightOf = (s: Settings) => s.weights.find((w) => w.code === FEATURE) as Weight
const shown = (v: number) => String(v).replace('.', ',')

test.beforeAll(async ({ request }) => {
  await loginApi(request)
  await request.post('/api/v1/model/settings/reset', { headers: originHeaders })
})

test.afterAll(async ({ request }) => {
  await loginApi(request)
  await request.post('/api/v1/model/settings/reset', { headers: originHeaders })
})

async function openWhatIf(page: Page) {
  await loginUi(page)
  await page.goto('/model')
  await page.getByRole('switch', { name: 'Что если' }).click()
  await expect(page.getByTestId('what-if')).toBeVisible()
}

/** Ставит вес «Ждёт оплаты» и ждёт ответ предпросмотра именно на это значение. */
async function setWeight(page: Page, value: number): Promise<Preview> {
  const response = page.waitForResponse(
    (r) =>
      r.url().includes('/api/v1/model/settings/preview') &&
      r.status() === 200 &&
      (r.request().postData() ?? '').includes(`"weight":${value}`),
  )
  const input = page.getByRole('textbox', { name: `Вес: ${TITLE}` })
  await input.fill(String(value))
  await input.blur()
  return (await (await response).json()) as Preview
}

test('вес «Ждёт оплаты» → видно изменение → «Отменить» → ничего не сохранилось', async ({ page }) => {
  const patches: string[] = []
  page.on('request', (r) => {
    if (r.method() === 'PATCH' || r.url().includes('/settings/reset')) patches.push(`${r.method()} ${r.url()}`)
  })
  await openWhatIf(page)
  const before = await settings(page)
  const original = weightOf(before).weight
  expect(original).toBeGreaterThan(0)

  const preview = await setWeight(page, 0)
  expect(preview.model_version).toBe(before.version)
  expect(preview.after.expected_loss).toBeLessThan(preview.before.expected_loss)

  const loss = page.getByTestId('what-if-expected-loss')
  await expect(loss.locator('[data-role=before]')).toContainText('было')
  const afterText = (await loss.locator('[data-role=after]').innerText()).trim()
  const beforeText = (await loss.locator('[data-role=before]').innerText()).replace('было', '').trim()
  expect(afterText).not.toBe(beforeText)
  await expect(loss).toContainText('−€')

  await page.getByRole('button', { name: 'Отменить' }).click()
  await expect(page.getByTestId('what-if')).toBeHidden()
  await expect(page.getByRole('textbox', { name: `Вес: ${TITLE}` })).toHaveValue(shown(original))

  // автосохранение обычного режима срабатывает через 400 мс: ждём с запасом
  await page.waitForTimeout(1500)
  expect(patches).toEqual([])
  const after = await settings(page)
  expect(after.version).toBe(before.version)
  expect(weightOf(after).weight).toBe(original)
})

test('«Применить» сохраняет, и пересчёт даёт те же цифры, что показало превью', async ({ page }) => {
  await openWhatIf(page)
  const before = await settings(page)
  const preview = await setWeight(page, 0)

  await page.getByRole('button', { name: 'Применить' }).click()
  await expect(page.getByTestId('what-if')).toBeHidden()
  await expect.poll(async () => (await settings(page)).version).toBe(before.version + 1)
  expect(weightOf(await settings(page)).weight).toBe(0)

  const summary = async () => (await (await page.request.get('/api/v1/analytics/summary')).json()) as Totals & { model_version: number }
  await expect.poll(async () => (await summary()).model_version, { timeout: 30_000 }).toBe(before.version + 1)
  if (preview.exact) {
    const real = await summary()
    expect(real.expected_loss).toBe(preview.after.expected_loss)
    expect(real.by_level).toEqual(preview.after.by_level)
  }
})
