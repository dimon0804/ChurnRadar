import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  CHUNK_RELOAD_KEY,
  CHUNK_RELOAD_WINDOW_MS,
  canReloadForChunk,
  installChunkErrorReload,
  isChunkLoadError,
  isChunkReloadPending,
  reloadOnceForChunk,
  resetChunkReloadState,
} from './chunk-error'

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial))
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    data,
  }
}

afterEach(() => resetChunkReloadState())

describe('isChunkLoadError', () => {
  it.each([
    ['Chrome', 'Failed to fetch dynamically imported module: http://localhost:8080/assets/model-B1x2.js'],
    ['Firefox', 'error loading dynamically imported module: http://localhost:8080/assets/model-B1x2.js'],
    ['Safari', 'Importing a module script failed.'],
    ['Vite CSS', 'Unable to preload CSS for /assets/model-B1x2.css'],
  ])('узнаёт сообщение %s', (_, message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true)
    expect(isChunkLoadError(message)).toBe(true)
  })

  it('узнаёт ChunkLoadError по имени', () => {
    const err = new Error('loading chunk 3 failed')
    err.name = 'ChunkLoadError'
    expect(isChunkLoadError(err)).toBe(true)
  })

  it('не путает с обычными ошибками', () => {
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false)
    expect(isChunkLoadError(new TypeError('Failed to fetch'))).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
    expect(isChunkLoadError({ status: 404 })).toBe(false)
  })
})

describe('reloadOnceForChunk', () => {
  it('первый раз перезагружает и ставит флаг', () => {
    const storage = memoryStorage()
    const reload = vi.fn()
    expect(reloadOnceForChunk(reload, storage, 1_000_000)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
    expect(storage.data.get(CHUNK_RELOAD_KEY)).toBe('1000000')
    expect(isChunkReloadPending()).toBe(true)
  })

  it('повторная ошибка сразу после перезагрузки не уводит в цикл', () => {
    const storage = memoryStorage({ [CHUNK_RELOAD_KEY]: '1000000' })
    const reload = vi.fn()
    expect(reloadOnceForChunk(reload, storage, 1_000_000 + 5_000)).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })

  it('после следующей пересборки снова можно перезагрузиться', () => {
    const storage = memoryStorage({ [CHUNK_RELOAD_KEY]: '1000000' })
    expect(canReloadForChunk(storage, 1_000_000 + CHUNK_RELOAD_WINDOW_MS + 1)).toBe(true)
  })

  it('без sessionStorage перезагружается один раз за жизнь вкладки', () => {
    const reload = vi.fn()
    expect(reloadOnceForChunk(reload, null, 1)).toBe(true)
    expect(reloadOnceForChunk(reload, null, 2)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('битое хранилище не ломает перезагрузку', () => {
    const storage = {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
    }
    const reload = vi.fn()
    expect(reloadOnceForChunk(reload, storage, 1)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
  })
})

describe('installChunkErrorReload', () => {
  it('гасит vite:preloadError, только когда пошла перезагрузка', () => {
    let handler: ((e: Event) => void) | null = null
    const target = { addEventListener: (_: string, h: (e: Event) => void) => void (handler = h) }
    installChunkErrorReload(target as unknown as Window)
    expect(handler).not.toBeNull()
    // в node нет window.location — подменяем глобально только на время проверки
    const reload = vi.fn()
    vi.stubGlobal('window', { location: { reload } })
    vi.stubGlobal('sessionStorage', memoryStorage())
    const event = { preventDefault: vi.fn() } as unknown as Event
    handler!(event)
    expect(reload).toHaveBeenCalledTimes(1)
    expect(event.preventDefault).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })
})
