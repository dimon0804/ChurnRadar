import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, type APIRequestContext, type Page } from '@playwright/test'

const here = dirname(fileURLToPath(import.meta.url))
export const repoRoot = resolve(process.env.COMPOSE_DIR ?? resolve(here, '../../..'))
export const baseURL = process.env.BASE_URL ?? 'http://localhost:8080'
export const ADMIN = { username: process.env.ADMIN_USERNAME ?? 'admin', password: process.env.ADMIN_PASSWORD ?? 'admin' }
export const METRICS_TOKEN = process.env.METRICS_TOKEN ?? 'churn-metrics-demo-token'

// unsafe requests with a session cookie must come from our own origin (CSRF guard)
export const originHeaders = { Origin: new URL(baseURL).origin }

export async function loginUi(page: Page, user = ADMIN) {
  await page.goto('/login')
  await page.getByLabel('Логин').fill(user.username)
  await page.getByLabel('Пароль', { exact: true }).fill(user.password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page).not.toHaveURL(/\/login/)
}

export async function loginApi(request: APIRequestContext, user = ADMIN) {
  const res = await request.post('/api/v1/auth/login', { data: user, headers: originHeaders })
  expect(res.status()).toBe(200)
}

export function composeServices(): string[] {
  const files = composeFiles()
  return files.some((f) => f.includes('docker-compose.mock.yml')) ? ['mock-core'] : ['api', 'scoring']
}

function composeFiles(): string[] {
  if (process.env.COMPOSE_FILES) return process.env.COMPOSE_FILES.split(/\s+/).filter((x) => x && x !== '-f')
  try {
    readFileSync(resolve(repoRoot, 'services/api/Dockerfile'))
    return ['docker-compose.yml']
  } catch {
    return ['docker-compose.yml', 'deploy/compose/docker-compose.mock.yml']
  }
}

export function compose(...args: string[]) {
  const fileArgs = composeFiles().flatMap((f) => ['-f', f])
  const project = process.env.COMPOSE_PROJECT ?? 'churn-radar'
  execFileSync('docker', ['compose', '-p', project, ...fileArgs, ...args], {
    cwd: repoRoot,
    stdio: 'inherit',
    env: { ...process.env, HTTP_PORT: new URL(baseURL).port || '80', PUBLIC_URL: new URL(baseURL).origin },
  })
}

export function sampleSnapshot() {
  return JSON.parse(readFileSync(resolve(repoRoot, 'data/sample/data.json'), 'utf-8'))
}

export type CustomerRow = { id: number; email: string; balance: number; total_replenished: number }

export async function listCustomers(request: APIRequestContext, query: Record<string, string>) {
  const res = await request.get('/api/v1/customers', { params: { page_size: '200', ...query } })
  expect(res.status()).toBe(200)
  return (await res.json()) as { items: CustomerRow[]; total: number }
}

export async function waitForStableText(page: Page, selector: string) {
  let last = ''
  await expect
    .poll(async () => {
      const now = (await page.locator(selector).innerText()).trim()
      const stable = now === last
      last = now
      return stable
    }, { intervals: [400] })
    .toBe(true)
  return last
}
