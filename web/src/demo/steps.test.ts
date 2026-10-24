import { describe, expect, it, vi } from 'vitest'
import type { ModelSettings, SimulatorCommand } from '@/api/types'
import { FEATURES } from './features'
import type { Target } from './spotlight'
import {
  applyWeightAction,
  DEMO_STEPS,
  demoWeight,
  isLocked,
  restoreCaseAction,
  stepIndex,
  WHAT_IF_CANCEL,
  WHAT_IF_OFF,
  type DemoApi,
  type DemoContext,
  type TopCustomer,
} from './steps'

const settings: ModelSettings = {
  version: 1,
  updated_at: '2026-10-24T10:00:00Z',
  horizon_days: 30,
  thresholds: { medium: 35, high: 65 },
  weights: [
    { code: 'autoprolong_off_expiring', title: 'Автопродление выключено', description: '', group: 'renewal', weight: 60, min: 0, max: 100, enabled: true },
    { code: 'prolong_wait', title: 'Ждёт оплаты', description: '', group: 'renewal', weight: 85, min: 0, max: 100, enabled: true },
  ],
}

function makeCtx(overrides: Partial<DemoApi> = {}, isAdmin = true) {
  const calls: string[] = []
  const simulatorCalls: SimulatorCommand[] = []
  const api: DemoApi = {
    topHighRisk: vi.fn(async (): Promise<TopCustomer | null> => ({ id: 7, email: 'user-0007@example.test', nextAction: 'Написать клиенту' })),
    settings: vi.fn(async () => settings),
    patchWeight: vi.fn(async () => settings),
    resetSettings: vi.fn(async () => {
      calls.push('reset')
      return settings
    }),
    simulator: vi.fn(async (command: SimulatorCommand) => {
      simulatorCalls.push(command)
      calls.push(command.generate ? 'generate' : command.running ? 'start' : 'stop')
      return { running: Boolean(command.running), interval_ms: 1500, events_emitted: 12 }
    }),
    restoreCaseData: vi.fn(async () => {
      calls.push('restore')
      return { accounts: 120 }
    }),
    logout: vi.fn(async () => undefined),
    ...overrides,
  }
  const ctx: DemoContext = {
    isAdmin,
    api,
    navigate: vi.fn(),
    spotlight: vi.fn(),
    waitFor: vi.fn(async () => null),
    click: vi.fn(async (target: Target) => {
      calls.push(`click:${target.text ?? target.selector}`)
      return true
    }),
    wait: vi.fn(async () => undefined),
    redirect: vi.fn(),
  }
  return { ctx, api, calls, simulatorCalls }
}

const step = (id: string) => {
  const found = DEMO_STEPS[stepIndex(id)]
  if (!found) throw new Error(`no step ${id}`)
  return found
}

describe('scenario', () => {
  it('has 6 to 12 steps with unique ids and short speaker notes', () => {
    expect(DEMO_STEPS.length).toBeGreaterThanOrEqual(6)
    expect(DEMO_STEPS.length).toBeLessThanOrEqual(12)
    expect(new Set(DEMO_STEPS.map((s) => s.id)).size).toBe(DEMO_STEPS.length)
    for (const s of DEMO_STEPS) {
      const sentences = s.say.split(/[.!?…](?=\s|$)/).filter((x) => x.trim()).length
      expect(sentences, s.id).toBeLessThanOrEqual(2)
      expect(s.show.length, s.id).toBeGreaterThan(0)
      expect(Boolean(s.run) || Boolean(s.links?.length), s.id).toBe(true)
    }
  })

  it('locks changing steps for analysts only', () => {
    const locked = DEMO_STEPS.filter((s) => isLocked(s, false)).map((s) => s.id)
    expect(locked).toEqual(['model', 'live', 'sources'])
    expect(DEMO_STEPS.filter((s) => isLocked(s, true))).toEqual([])
    for (const s of DEMO_STEPS) {
      for (const a of s.actions ?? []) {
        const mutating = ['apply-weight', 'reset-settings', 'stop-stream', 'restore-case'].includes(a.id)
        expect(Boolean(a.adminOnly), a.id).toBe(mutating)
      }
    }
  })

  it('opens the overview and highlights the money cards', async () => {
    const { ctx } = makeCtx()
    await step('overview').run?.(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/')
    const targets = vi.mocked(ctx.spotlight).mock.calls[0]?.[0] ?? []
    expect(targets.map((t) => t.selector)).toEqual(['[data-testid="kpi-high"]', '[data-testid="kpi-expected-loss"]'])
  })

  it('filters the table to high risk', async () => {
    const { ctx } = makeCtx()
    await step('customers').run?.(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/customers?level=high')
    const balance = step('customers').actions?.find((a) => a.id === 'sort-balance')
    await balance?.run(ctx)
    expect(ctx.navigate).toHaveBeenLastCalledWith('/customers?sort=balance&order=desc')
  })

  it('opens the riskiest customer with the next action', async () => {
    const { ctx } = makeCtx()
    const message = await step('customer').run?.(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/customers/7?level=high')
    expect(message).toContain('Написать клиенту')
  })

  it('explains when nobody is at high risk', async () => {
    const { ctx } = makeCtx({ topHighRisk: async () => null })
    const message = await step('customer').run?.(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/customers')
    expect(message).toMatch(/нет клиентов высокого риска/)
  })

  it('follows the jury order', () => {
    expect(DEMO_STEPS.map((s) => s.id)).toEqual([
      'overview',
      'contact-today',
      'chart',
      'customers',
      'customer',
      'marketing',
      'model',
      'quality',
      'live',
      'sources',
      'ops',
      'roles',
    ])
  })

  it('shows the contact list with the mass email button on the overview', async () => {
    const { ctx } = makeCtx()
    await step('contact-today').run?.(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/')
    const targets = vi.mocked(ctx.spotlight).mock.calls[0]?.[0] ?? []
    expect(targets.map((t) => t.selector)).toEqual(['[data-testid="contact-today"]', '[data-testid="contact-today-mass"]'])
  })

  it('turns on what-if on the model page without saving anything', async () => {
    const { ctx, api, calls } = makeCtx()
    const message = await step('model').run?.(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/model')
    expect(ctx.click).toHaveBeenCalledWith(WHAT_IF_OFF)
    expect(calls).toEqual(['click:Что если'])
    expect(api.patchWeight).not.toHaveBeenCalled()
    expect(api.resetSettings).not.toHaveBeenCalled()
    expect(message).toContain('Ждёт оплаты')
  })

  it('looks for the what-if switch only while it is off', () => {
    expect(WHAT_IF_OFF.selector).toContain('[aria-checked="false"]')
    expect(WHAT_IF_OFF.text).toBe('Что если')
  })

  it('opens the marketing page on LTV and the retention block', async () => {
    const { ctx } = makeCtx({}, false)
    await step('marketing').run?.(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/marketing')
    const targets = vi.mocked(ctx.spotlight).mock.calls[0]?.[0] ?? []
    expect(targets.map((t) => t.selector)).toEqual([
      '[data-testid="mkt-ltv"]',
      '[data-testid="mkt-ltv-cac"]',
      '[data-testid="mkt-retention"]',
    ])
    expect(isLocked(step('marketing'), false)).toBe(false)
  })

  it('opens the model quality page on lift and ROC AUC', async () => {
    const { ctx } = makeCtx()
    await step('quality').run?.(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/quality')
    const targets = vi.mocked(ctx.spotlight).mock.calls[0]?.[0] ?? []
    expect(targets.map((t) => t.selector)).toEqual(['[data-testid="quality-lift"]', '[data-testid="quality-auc"]'])
    expect(isLocked(step('quality'), false)).toBe(false)
  })

  it('points at the alerts bell while the stream runs', async () => {
    const { ctx } = makeCtx()
    await step('live').run?.(ctx)
    const targets = vi.mocked(ctx.spotlight).mock.calls[0]?.[0] ?? []
    expect(targets.some((t) => t.selector.includes('Уведомления'))).toBe(true)
  })

  it('opens the sources section on the data page without changing anything', async () => {
    const { ctx, api } = makeCtx()
    await step('sources').run?.(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/data')
    const targets = vi.mocked(ctx.spotlight).mock.calls[0]?.[0] ?? []
    expect(targets.map((t) => t.selector)).toEqual(['[data-testid="sources"]'])
    expect(api.simulator).not.toHaveBeenCalled()
  })

  it('applies the prolong_wait weight after closing the what-if draft', async () => {
    const { ctx, api, calls } = makeCtx()
    const message = await applyWeightAction.run(ctx)
    expect(ctx.navigate).toHaveBeenCalledWith('/model')
    expect(ctx.click).toHaveBeenCalledWith(WHAT_IF_CANCEL)
    expect(calls).toEqual(['click:Отменить'])
    expect(api.patchWeight).toHaveBeenCalledTimes(1)
    const [code, weight] = vi.mocked(api.patchWeight).mock.calls[0] ?? []
    expect(code).toBe('prolong_wait')
    expect(weight).not.toBe(85)
    expect(weight).toBeGreaterThanOrEqual(0)
    expect(weight).toBeLessThanOrEqual(100)
    expect(message).toContain('Ждёт оплаты')
  })

  it('generates a bigger set before starting the stream', async () => {
    const { ctx, simulatorCalls } = makeCtx()
    await step('live').run?.(ctx)
    expect(simulatorCalls).toEqual([{ generate: { accounts: 5000, seed: 42 } }, { running: true, interval_ms: 1500 }])
  })

  it('stops the stream before bringing back the case data', async () => {
    const { ctx, calls } = makeCtx()
    const message = await restoreCaseAction.run(ctx)
    expect(calls).toEqual(['stop', 'restore'])
    expect(message).toContain('120')
  })

  it('sends the presenter to the login page for the analyst', async () => {
    const { ctx, api } = makeCtx()
    await step('roles').actions?.[0]?.run(ctx)
    expect(api.logout).toHaveBeenCalled()
    expect(ctx.redirect).toHaveBeenCalledWith('/login?next=%2Fmodel%3Fdemo%3D1')
  })
})

describe('demoWeight', () => {
  it('moves the weight far enough and stays in range', () => {
    expect(demoWeight(85, 0, 100)).toBe(20)
    expect(demoWeight(10, 0, 100)).toBe(60)
    expect(demoWeight(4.5, 0, 5)).toBeGreaterThanOrEqual(0)
    expect(demoWeight(100, 0, 100)).toBe(35)
  })
})

describe('features', () => {
  it('links every item to a step, a page or an address', () => {
    expect(new Set(FEATURES.map((f) => f.id)).size).toBe(FEATURES.length)
    for (const f of FEATURES) {
      if (f.target.kind === 'step') expect(stepIndex(f.target.step), f.id).toBeGreaterThanOrEqual(0)
      if (f.target.kind === 'route') expect(f.target.to.startsWith('/'), f.id).toBe(true)
      if (f.target.kind === 'url') expect(f.target.href, f.id).toMatch(/^(https?:\/\/|\/)/)
    }
    expect(FEATURES.some((f) => f.target.kind === 'url' && f.target.href.endsWith('/docs/calibration.md'))).toBe(true)
  })
})
