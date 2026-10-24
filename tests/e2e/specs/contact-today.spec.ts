import { expect, test, type APIRequestContext } from '@playwright/test'
import { loginApi, loginUi, originHeaders } from './helpers'

// «С кем связаться сегодня»: порядок по деньгам, отметки «Решено»/«Отложено», выгрузка CSV.
// Спек сам снимает все отметки, которые поставил, — стенд остаётся как был.
test.describe.configure({ mode: 'serial' })

const ANALYST = {
  username: process.env.ANALYST_USERNAME ?? 'analyst',
  password: process.env.ANALYST_PASSWORD ?? 'analyst',
}

type QueueItem = { id: number; email: string; risk_level: string; expected_loss: number }
type Queue = { items: QueueItem[]; queue_total: number; marked_total: number }

const touched = new Set<number>()

async function queue(request: APIRequestContext, marked = false): Promise<Queue> {
  const res = await request.get('/api/v1/contact-today', { params: { limit: '100', marked: String(marked) } })
  expect(res.status()).toBe(200)
  return (await res.json()) as Queue
}

test.afterAll(async ({ request }) => {
  await loginApi(request)
  for (const id of touched) {
    const res = await request.delete(`/api/v1/customers/${id}/action`, { headers: originHeaders })
    expect(res.status(), `снять отметку с ${id}`).toBe(200)
  }
})

test('очередь: только клиенты с риском, по убыванию ожидаемой потери', async ({ request }) => {
  await loginApi(request, ANALYST)
  const q = await queue(request)
  expect(q.items.length).toBeGreaterThan(0)
  for (const item of q.items) {
    expect(item.risk_level).not.toBe('unknown')
    expect(item.expected_loss).toBeGreaterThan(0)
  }
  const losses = q.items.map((i) => i.expected_loss)
  expect(losses).toEqual([...losses].sort((a, b) => b - a))

  // в данных кейса 4 high и 2 medium: все шестеро с ненулевой потерей, unknown не попадают
  const res = await request.get('/api/v1/customers', { params: { level: 'high,medium', page_size: '200' } })
  const atRisk = ((await res.json()) as { items: QueueItem[] }).items.filter((c) => c.expected_loss > 0)
  expect(q.queue_total + q.marked_total).toBe(atRisk.length)
})

test('блок на обзоре: топ-5 по деньгам, «Решено» убирает клиента, «Вернуть» — возвращает', async ({ page, request }) => {
  await loginApi(request, ANALYST)
  const top = (await queue(request)).items.slice(0, 5)

  await loginUi(page, ANALYST)
  const block = page.getByTestId('contact-today')
  await expect(block.getByText('Сортируем по деньгам, которые можно спасти')).toBeVisible()
  const rows = block.getByTestId('contact-today-row')
  await expect(rows).toHaveCount(top.length)
  await expect(rows.first().getByRole('link')).toHaveText(top[0]!.email)
  const shown = (await block.getByTestId('contact-today-loss').allInnerTexts()).map((t) => Number(t.replace(/\D/g, '')))
  expect(shown).toEqual([...shown].sort((a, b) => b - a))
  await expect(block).not.toContainText(/звон/i)

  // «Решено» по первому — уходит из списка
  const first = top[0]!
  touched.add(first.id)
  await expect(block.getByRole('link', { name: first.email, exact: true })).toBeVisible()
  await rows.first().getByRole('button', { name: 'Отметить' }).click()
  await page.getByRole('menuitem', { name: 'Клиент ответил, решено' }).click()
  await expect(block.getByRole('link', { name: first.email, exact: true })).toHaveCount(0)

  // «Отложить» по следующему — тоже уходит
  const second = top[1]
  if (second) {
    touched.add(second.id)
    await expect(block.getByRole('link', { name: second.email, exact: true })).toBeVisible()
    const row = rows.filter({ has: page.getByRole('link', { name: second.email, exact: true }) })
    await row.getByRole('button', { name: 'Отметить' }).click()
    await page.getByRole('menuitem', { name: 'На 3 дня' }).click()
    await expect(block.getByRole('link', { name: second.email, exact: true })).toHaveCount(0)
  }

  // в отмеченных — оба, с журналом «кто отметил»
  await block.getByRole('button', { name: /Показать отмеченные/ }).click()
  await expect(block.getByRole('link', { name: first.email, exact: true })).toBeVisible()
  await expect(block.getByText('Решено', { exact: true }).first()).toBeVisible()
  const state = await request.get(`/api/v1/customers/${first.id}/action`)
  const body = (await state.json()) as { action: { status: string; updated_by: string } }
  expect(body.action.status).toBe('resolved')
  expect(body.action.updated_by).toBe(ANALYST.username)

  // «Вернуть» — снова в очереди на своём месте
  const markedRow = block.getByTestId('contact-today-row').filter({ has: page.getByRole('link', { name: first.email, exact: true }) })
  await markedRow.getByRole('button', { name: 'Вернуть' }).click()
  await block.getByRole('button', { name: 'К списку' }).click()
  await expect(block.getByTestId('contact-today-row').first().getByRole('link')).toHaveText(first.email)
})

test('выгрузка CSV: UTF-8 с BOM, «;», те же фильтры, что у списка', async ({ page, request }) => {
  await loginApi(request, ANALYST)
  const res = await request.get('/api/v1/customers/export.csv', { params: { level: 'high', sort: 'expected_loss', order: 'desc' } })
  expect(res.status()).toBe(200)
  expect(res.headers()['content-type']).toContain('text/csv')
  expect(res.headers()['content-disposition']).toContain('attachment')
  const raw = await res.body()
  expect([...raw.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf])
  const lines = raw.toString('utf-8').replace(/^﻿/, '').trim().split('\r\n')
  expect(lines[0]).toBe(
    'ID;Email;Уровень риска;Риск, баллы;Ожидаемая потеря, €;Баланс, €;Пополнения всего, €;Главная причина;Что сделать;Статус',
  )
  const list = await request.get('/api/v1/customers', { params: { level: 'high', sort: 'expected_loss', order: 'desc', page_size: '200' } })
  const ids = ((await list.json()) as { items: QueueItem[] }).items.map((c) => String(c.id))
  expect(lines.slice(1).map((l) => l.split(';')[0])).toEqual(ids)
  for (const line of lines.slice(1)) expect(line.split(';')[2]).toBe('Высокий')

  // кнопка на странице «Клиенты» несёт текущие фильтры
  await loginUi(page, ANALYST)
  await page.goto('/customers?level=high&sort=expected_loss&order=desc')
  const href = await page.getByTestId('customers-csv').getAttribute('href')
  expect(href).toBe('/api/v1/customers/export.csv?sort=expected_loss&order=desc&level=high')
  const download = page.waitForEvent('download')
  await page.getByTestId('customers-csv').click()
  expect((await download).suggestedFilename()).toMatch(/^churnradar-clients-\d{4}-\d{2}-\d{2}\.csv$/)
})
