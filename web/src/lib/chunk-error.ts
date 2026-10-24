/**
 * Открытая вкладка после пересборки панели просит файлы старой сборки, а их уже нет.
 * Узнаём такую ошибку и один раз перезагружаем страницу: подтянется свежий index.html
 * с новыми именами файлов. Флаг в sessionStorage не даёт уйти в цикл перезагрузок.
 */

/** Сообщения браузеров об оборванной загрузке модуля: Chrome, Firefox, Safari и Vite. */
const CHUNK_ERROR_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /unable to preload css/i,
  /'text\/html' is not a valid javascript mime type/i,
]

export const CHUNK_RELOAD_KEY = 'cr-chunk-reload'
/** Если после перезагрузки за это время ошибка повторилась — сборка и правда сломана, не крутимся. */
export const CHUNK_RELOAD_WINDOW_MS = 60_000

export function isChunkLoadError(error: unknown): boolean {
  if (!error) return false
  if (typeof error === 'object' && 'name' in error && (error as { name?: unknown }).name === 'ChunkLoadError') return true
  const message =
    typeof error === 'string'
      ? error
      : typeof error === 'object' && 'message' in error && typeof (error as { message?: unknown }).message === 'string'
        ? (error as { message: string }).message
        : ''
  return CHUNK_ERROR_PATTERNS.some((re) => re.test(message))
}

type FlagStorage = Pick<Storage, 'getItem' | 'setItem'>

function safeStorage(): FlagStorage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

/** Можно ли ещё раз перезагрузиться: недавней попытки не было. */
export function canReloadForChunk(storage: FlagStorage | null = safeStorage(), now = Date.now()): boolean {
  if (!storage) return true
  try {
    const last = Number(storage.getItem(CHUNK_RELOAD_KEY))
    return !Number.isFinite(last) || last <= 0 || now - last > CHUNK_RELOAD_WINDOW_MS
  } catch {
    return true
  }
}

let reloadPending = false

export function isChunkReloadPending(): boolean {
  return reloadPending
}

/**
 * Перезагружает страницу, если недавно этого не делали. Возвращает true, если перезагрузка пошла.
 * Без sessionStorage (приватный режим с запретом) перезагружаемся один раз за жизнь вкладки.
 */
export function reloadOnceForChunk(
  reload: () => void = () => window.location.reload(),
  storage: FlagStorage | null = safeStorage(),
  now = Date.now(),
): boolean {
  if (reloadPending) return true
  if (!canReloadForChunk(storage, now)) return false
  try {
    storage?.setItem(CHUNK_RELOAD_KEY, String(now))
  } catch {
    // без хранилища защита от цикла — флаг в памяти ниже
  }
  reloadPending = true
  reload()
  return true
}

/** Для тестов. */
export function resetChunkReloadState() {
  reloadPending = false
}

/**
 * Vite сообщает об оборванной подгрузке чанка событием vite:preloadError.
 * Если можно перезагрузиться — гасим ошибку и перезагружаемся; если нет — она дойдёт
 * до экрана ошибки маршрута, и там будет «Панель обновилась».
 */
export function installChunkErrorReload(target: Pick<Window, 'addEventListener'> = window) {
  target.addEventListener('vite:preloadError', (event) => {
    if (reloadOnceForChunk()) event.preventDefault()
  })
}
