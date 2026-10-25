import { AlertTriangle, Lock, RotateCw, SearchX } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { errorMessage, isApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode
  title: string
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center px-6 py-12 text-center', className)}>
      <div className="mb-3 flex size-11 items-center justify-center rounded-[13px] bg-brand-soft text-brand [&_svg]:size-5">
        {icon ?? <SearchX />}
      </div>
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description && <p className="mt-1 max-w-sm text-[13px] leading-5 text-muted-foreground">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  title = 'Не удалось загрузить данные',
  className,
}: {
  error: unknown
  onRetry?: () => void
  title?: string
  className?: string
}) {
  const requestId = isApiError(error) ? error.requestId : null
  return (
    <div role="alert" className={cn('flex flex-col items-center justify-center px-6 py-12 text-center', className)}>
      <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-risk-high-bg text-risk-high">
        <AlertTriangle className="size-5" />
      </div>
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 max-w-sm text-[13px] leading-5 text-muted-foreground">{errorMessage(error)}</p>
      {requestId && <p className="mt-1 text-xs text-subtle-foreground num">Код запроса: {requestId}</p>}
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
          <RotateCw />
          Повторить
        </Button>
      )}
    </div>
  )
}

export function ForbiddenState() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center px-6 py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Lock className="size-5" />
      </div>
      <h1 className="text-lg font-semibold tracking-tight">Этот раздел только для администраторов</h1>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">
        Настройки модели и загрузку данных меняет администратор. Если вам нужен доступ, попросите его выдать роль
        «Администратор».
      </p>
      <Button asChild variant="outline" className="mt-6">
        <Link to="/">Вернуться к обзору</Link>
      </Button>
    </div>
  )
}

export function NotFoundState() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center px-6 py-24 text-center">
      <p className="text-5xl font-semibold tracking-tight text-subtle-foreground num">404</p>
      <h1 className="mt-4 text-lg font-semibold">Такой страницы нет</h1>
      <p className="mt-2 text-sm text-muted-foreground">Возможно, ссылка устарела или в ней опечатка.</p>
      <Button asChild variant="outline" className="mt-6">
        <Link to="/">На главную</Link>
      </Button>
    </div>
  )
}
