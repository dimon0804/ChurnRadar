import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ComponentProps, ReactNode } from 'react'
import { cn } from '@/lib/utils'

export const Dialog = DialogPrimitive.Root
export const DialogClose = DialogPrimitive.Close

/** Окно по центру экрана: заголовок, прокручиваемое тело и закреплённый низ с кнопками. */
export function DialogContent({
  title,
  description,
  footer,
  children,
  className,
  ...props
}: Omit<ComponentProps<typeof DialogPrimitive.Content>, 'title'> & {
  title: ReactNode
  description?: ReactNode
  footer?: ReactNode
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/30 data-[state=closed]:animate-overlay-out data-[state=open]:animate-overlay-in dark:bg-black/50" />
      <DialogPrimitive.Content
        className={cn(
          'fixed top-1/2 left-1/2 z-50 flex max-h-[min(760px,calc(100dvh-32px))] w-[min(640px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-card shadow-pop outline-none data-[state=open]:animate-pop-in',
          // на телефоне — на весь экран: так удобнее печатать и не наезжает клавиатура
          'max-sm:inset-0 max-sm:h-dvh max-sm:max-h-dvh max-sm:w-full max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-none max-sm:border-0 max-sm:pb-[env(safe-area-inset-bottom)]',
          className,
        )}
        {...props}
      >
        <div className="flex items-start gap-3 border-b border-border px-4 py-4 sm:px-6">
          <div className="min-w-0 flex-1">
            <DialogPrimitive.Title className="text-base font-semibold">{title}</DialogPrimitive.Title>
            {description ? (
              <DialogPrimitive.Description className="mt-1 text-[13px] leading-5 text-muted-foreground">
                {description}
              </DialogPrimitive.Description>
            ) : (
              <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
            )}
          </div>
          <DialogPrimitive.Close
            className="-mr-2 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label="Закрыть"
          >
            <X className="size-4" />
          </DialogPrimitive.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">{children}</div>
        {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3 sm:px-6">{footer}</div>}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
}

export function Textarea({ className, ...props }: ComponentProps<'textarea'>) {
  return (
    <textarea
      className={cn(
        'flex w-full min-w-0 rounded-md border border-input bg-card px-3 py-2 text-sm leading-6 text-foreground shadow-card transition-[border-color,box-shadow] outline-none placeholder:text-subtle-foreground',
        'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/20 focus-visible:outline-none',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}
