// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelSettingsPatch, ModelSettingsPreview } from '@/api/types'
import { PREVIEW_DEBOUNCE_MS, useSettingsPreview, WhatIfBar, WhatIfChanges } from './what-if'

const totals = (loss: number, high: number, share: number) => ({
  horizon_days: 30,
  thresholds: { medium: 35, high: 65 },
  expected_loss: loss,
  exposure: loss * 2,
  services_at_risk: high,
  revenue_at_risk_share: share,
  by_level: { high, medium: 2, low: 6 - high, unknown: 4 },
})

const PREVIEW: ModelSettingsPreview = {
  model_version: 7,
  exact: true,
  customers_total: 10,
  sample_size: 10,
  before: totals(22_978_300, 4, 0.31),
  after: totals(22_103_000, 3, 0.298),
  changes_total: 1,
  into_high: 0,
  out_of_high: 1,
  changes: [
    {
      id: 10,
      email: 'user-0010@example.test',
      level_before: 'high',
      level_after: 'medium',
      score_before: 65.7,
      score_after: 48.2,
      expected_loss_before: 648,
      expected_loss_after: 470,
    },
  ],
  duration_ms: 12,
}

type Call = {
  body: ModelSettingsPatch
  signal: AbortSignal
  resolve: (p: ModelSettingsPreview) => void
  fail: (status: number, code: string, message: string) => void
}

describe('«Что если»', () => {
  let root: Root
  let container: HTMLDivElement
  let calls: Call[]
  let client: QueryClient
  const onApply = vi.fn()
  const onCancel = vi.fn()

  function Harness({ patch }: { patch: ModelSettingsPatch | null }) {
    const preview = useSettingsPreview(patch)
    return createElement(
      'div',
      null,
      createElement(WhatIfBar, { preview, onApply, onCancel, applying: false }),
      createElement(WhatIfChanges, { preview: preview.data }),
    )
  }

  const render = (patch: ModelSettingsPatch | null) =>
    act(() => root.render(createElement(QueryClientProvider, { client }, createElement(Harness, { patch }))))

  const wait = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)))

  const button = (name: string) =>
    [...container.querySelectorAll('button')].find((b) => b.textContent?.includes(name)) as HTMLButtonElement

  beforeEach(() => {
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    vi.useFakeTimers()
    calls = []
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init: RequestInit) => {
        expect(url).toBe('/api/v1/model/settings/preview')
        expect(init.method).toBe('POST')
        return new Promise<Response>((resolve, reject) => {
          const signal = init.signal as AbortSignal
          signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
          calls.push({
            body: JSON.parse(String(init.body)) as ModelSettingsPatch,
            signal,
            resolve: (p) => resolve(new Response(JSON.stringify(p), { status: 200 })),
            fail: (status, code, message) =>
              resolve(new Response(JSON.stringify({ error: { code, message } }), { status })),
          })
        })
      }),
    )
    client = new QueryClient()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    onApply.mockClear()
    onCancel.mockClear()
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('без изменений ничего не запрашивает и «Применить» недоступно', async () => {
    render({})
    await wait(PREVIEW_DEBOUNCE_MS * 2)
    expect(calls).toHaveLength(0)
    expect(button('Применить').disabled).toBe(true)
    expect(container.textContent).toContain('Пока ничего не сохраняется')
  })

  it('ждёт 300 мс после последнего движения и шлёт только последнее значение', async () => {
    render(null) // как на странице: хук живёт и до включения режима
    render({ weights: [{ code: 'prolong_wait', weight: 80 }] })
    await wait(100)
    render({ weights: [{ code: 'prolong_wait', weight: 50 }] })
    await wait(100)
    render({ weights: [{ code: 'prolong_wait', weight: 0 }] })
    await wait(PREVIEW_DEBOUNCE_MS - 1)
    expect(calls).toHaveLength(0)
    await wait(1)
    expect(calls).toHaveLength(1)
    expect(calls[0]?.body).toEqual({ weights: [{ code: 'prolong_wait', weight: 0 }] })
  })

  it('обрывает устаревший запрос и показывает «было → станет»', async () => {
    render({ weights: [{ code: 'prolong_wait', weight: 50 }] })
    await wait(PREVIEW_DEBOUNCE_MS)
    expect(calls).toHaveLength(1)
    render({ weights: [{ code: 'prolong_wait', weight: 0 }] })
    await wait(PREVIEW_DEBOUNCE_MS)
    expect(calls).toHaveLength(2)
    expect(calls[0]?.signal.aborted).toBe(true)
    expect(calls[1]?.signal.aborted).toBe(false)
    expect(container.textContent).toContain('Считаем')

    await act(async () => calls[1]?.resolve(PREVIEW))
    await wait(0)
    const loss = container.querySelector('[data-testid="what-if-expected-loss"]')
    expect(loss?.querySelector('[data-role="after"]')?.textContent).toBe('€221 030')
    expect(loss?.querySelector('[data-role="before"]')?.textContent).toContain('было €229 783')
    expect(loss?.textContent).toContain('−€8 753')
    const high = container.querySelector('[data-testid="what-if-levels"] [data-level="high"]')
    expect(high?.textContent).toContain('4')
    expect(high?.textContent).toContain('−1')
    expect(container.textContent).toContain('1 выйдет из высокого')
    const rows = container.querySelectorAll('[data-testid="what-if-change"]')
    expect(rows).toHaveLength(1)
    expect(rows[0]?.getAttribute('data-kind')).toBe('out_of_high')
    expect(rows[0]?.textContent).toContain('user-0010@example.test')
    expect(rows[0]?.textContent).toContain('score 66 → 48')
  })

  it('кнопки «Применить» и «Отменить» вызывают обработчики', async () => {
    render({ horizon_days: 14 })
    await wait(PREVIEW_DEBOUNCE_MS)
    await act(async () => calls[0]?.resolve(PREVIEW))
    await wait(0)
    act(() => button('Применить').click())
    act(() => button('Отменить').click())
    expect(onApply).toHaveBeenCalledTimes(1)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('на большой базе предупреждает, что цифры приблизительные', async () => {
    render({ horizon_days: 14 })
    await wait(PREVIEW_DEBOUNCE_MS)
    await act(async () => calls[0]?.resolve({ ...PREVIEW, exact: false, sample_size: 2000, customers_total: 100_000 }))
    await wait(0)
    expect(container.querySelector('[data-testid="what-if-approximate"]')?.textContent).toContain('приблизительный')
  })

  it('при таймауте показывает ошибку и даёт повторить', async () => {
    render({ horizon_days: 14 })
    await wait(PREVIEW_DEBOUNCE_MS)
    await act(async () => calls[0]?.fail(504, 'preview_timeout', 'Предпросмотр не уложился во время, попробуйте ещё раз'))
    await wait(0)
    expect(container.textContent).toContain('Не удалось посчитать: Предпросмотр не уложился во время')
    act(() => button('Повторить').click())
    await wait(0)
    expect(calls).toHaveLength(2)
    expect(calls[1]?.body).toEqual({ horizon_days: 14 })
    await act(async () => calls[1]?.resolve(PREVIEW))
    await wait(0)
    expect(container.textContent).not.toContain('Не удалось посчитать')
    expect(container.textContent).toContain('1 выйдет из высокого')
  })
})
