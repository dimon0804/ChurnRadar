import { AlertTriangle, RefreshCw, Sparkles } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { isRouteErrorResponse, Link, useRouteError } from 'react-router'
import { NotFoundState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { canReloadForChunk, isChunkLoadError, isChunkReloadPending, reloadOnceForChunk } from '@/lib/chunk-error'
import { cn } from '@/lib/utils'

function Frame({ fullScreen, children }: { fullScreen?: boolean; children: ReactNode }) {
  return (
    <div
      role="alert"
      className={cn(
        'mx-auto flex max-w-md flex-col items-center px-6 text-center',
        fullScreen ? 'min-h-dvh justify-center py-12' : 'py-20 sm:py-24',
      )}
    >
      {children}
    </div>
  )
}

function reloadPage() {
  window.location.reload()
}

/** Панель пересобрали, а вкладка просит файлы старой сборки. */
function ChunkErrorScreen({ fullScreen }: { fullScreen?: boolean }) {
  // решение принимаем при рендере, а перезагружаем в эффекте
  const willReload = isChunkReloadPending() || canReloadForChunk()
  useEffect(() => {
    if (willReload && !isChunkReloadPending()) reloadOnceForChunk()
  }, [willReload])

  if (willReload) {
    return (
      <Frame fullScreen={fullScreen}>
        <RefreshCw className="size-5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden />
        <p className="mt-3 text-sm text-muted-foreground" data-testid="chunk-reloading">
          Загружаем новую версию панели…
        </p>
      </Frame>
    )
  }
  return (
    <Frame fullScreen={fullScreen}>
      <div className="mb-4 flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Sparkles className="size-5" aria-hidden />
      </div>
      <h1 className="text-lg font-semibold tracking-tight" data-testid="chunk-error">
        Панель обновилась
      </h1>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">
        Пока страница была открыта, вышла новая версия. Обновите страницу, чтобы её загрузить. Несохранённых данных
        здесь нет.
      </p>
      <Button className="mt-6" onClick={reloadPage}>
        <RefreshCw />
        Обновить страницу
      </Button>
    </Frame>
  )
}

/**
 * Экран ошибки маршрута вместо стандартного «Unexpected Application Error!».
 * fullScreen — для корневых маршрутов, где шапки панели нет.
 */
export function RouteError({ fullScreen = false }: { fullScreen?: boolean }) {
  const error = useRouteError()

  if (isChunkLoadError(error)) return <ChunkErrorScreen fullScreen={fullScreen} />
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFoundState />

  const details =
    isRouteErrorResponse(error)
      ? `${error.status} ${error.statusText}`
      : error instanceof Error
        ? error.message
        : typeof error === 'string'
          ? error
          : ''

  return (
    <Frame fullScreen={fullScreen}>
      <div className="mb-4 flex size-12 items-center justify-center rounded-full bg-risk-high-bg text-risk-high">
        <AlertTriangle className="size-5" aria-hidden />
      </div>
      <h1 className="text-lg font-semibold tracking-tight">Что-то пошло не так</h1>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">
        Страница не открылась из-за ошибки в панели. Обновите страницу; если не поможет — вернитесь на обзор и
        попробуйте ещё раз.
      </p>
      {details && (
        <details className="mt-4 w-full text-left">
          <summary className="cursor-pointer text-xs text-subtle-foreground">Подробности для разработчика</summary>
          <pre className="mt-2 max-h-40 overflow-auto rounded-md bg-muted p-3 text-xs break-words whitespace-pre-wrap text-muted-foreground">
            {details}
          </pre>
        </details>
      )}
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <Button onClick={reloadPage}>
          <RefreshCw />
          Обновить страницу
        </Button>
        <Button asChild variant="outline">
          <Link to="/" reloadDocument>
            На главную
          </Link>
        </Button>
      </div>
    </Frame>
  )
}
