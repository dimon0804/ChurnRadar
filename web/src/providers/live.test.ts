// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LiveProvider } from './live'

type Handler = (event: MessageEvent<string>) => void

class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []

  readyState = FakeEventSource.CONNECTING
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  private handlers = new Map<string, Handler[]>()

  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, handler: Handler) {
    this.handlers.set(type, [...(this.handlers.get(type) ?? []), handler])
  }

  close() {
    this.readyState = FakeEventSource.CLOSED
  }

  open() {
    this.readyState = FakeEventSource.OPEN
    this.onopen?.()
  }

  fail() {
    this.onerror?.()
  }

  dispatch(type: string, data: string) {
    this.handlers.get(type)?.forEach((fn) => fn(new MessageEvent(type, { data })))
  }
}

describe('LiveProvider', () => {
  let root: Root
  let container: HTMLDivElement
  let invalidate: ReturnType<typeof vi.fn>

  beforeEach(() => {
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    const queryClient = new QueryClient()
    invalidate = vi.fn(() => Promise.resolve())
    queryClient.invalidateQueries = invalidate as unknown as QueryClient['invalidateQueries']
    container = document.createElement('div')
    root = createRoot(container)
    act(() => {
      root.render(createElement(QueryClientProvider, { client: queryClient }, createElement(LiveProvider, null)))
    })
  })

  afterEach(() => {
    act(() => root.unmount())
    vi.unstubAllGlobals()
  })

  const source = () => {
    const s = FakeEventSource.instances.at(-1)
    if (!s) throw new Error('EventSource not created')
    return s
  }

  /** аргументы invalidateAll: всё, кроме ключа текущего пользователя */
  const invalidatedAll = () =>
    invalidate.mock.calls.some(([filters]) => typeof (filters as { predicate?: unknown })?.predicate === 'function')

  it('перечитывает все данные по событию resync', () => {
    act(() => source().open())
    expect(invalidate).not.toHaveBeenCalled()

    act(() => source().dispatch('resync', '{"reason":"nats_reconnected"}'))
    expect(invalidate).toHaveBeenCalledTimes(1)
    expect(invalidatedAll()).toBe(true)
  })

  it('resync срабатывает так же, как переподключение после ошибки', () => {
    act(() => source().open())
    act(() => source().fail())
    act(() => source().open())
    expect(invalidate).toHaveBeenCalledTimes(1)
    const [onReconnect] = invalidate.mock.calls[0] ?? []

    invalidate.mockClear()
    act(() => source().dispatch('resync', '{"reason":"unknown_last_event_id"}'))
    expect(invalidate).toHaveBeenCalledTimes(1)
    const [onResync] = invalidate.mock.calls[0] ?? []

    const predicate = (f: unknown) => (f as { predicate: (q: { queryKey: unknown[] }) => boolean }).predicate
    for (const key of [['me'], ['customers'], ['summary'], ['settings']]) {
      expect(predicate(onResync)({ queryKey: key })).toBe(predicate(onReconnect)({ queryKey: key }))
    }
  })
})
