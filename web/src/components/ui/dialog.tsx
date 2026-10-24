import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** Модальное окно по центру: формы создания и редактирования. */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: ReactNode
  children: ReactNode
  footer?: ReactNode
  className?: string
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/30 data-[state=closed]:animate-overlay-out data-[state=open]:animate-overlay-in dark:bg-black/50" />
        <DialogPrimitive.Content
          className={cn(
            'fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100vh-32px)] w-[min(480px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-card shadow-pop outline-none data-[state=open]:animate-pop-in',
            className,
          )}
          {...(description ? {} : { 'aria-describedby': undefined })}
        >
          <div className="flex items-start gap-3 px-6 pt-5">
            <div className="min-w-0 flex-1">
              <DialogPrimitive.Title className="text-base font-semibold">{title}</DialogPrimitive.Title>
              {description && (
                <DialogPrimitive.Description className="mt-1.5 text-[13px] leading-5 text-muted-foreground">
                  {description}
                </DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close
              className="-mt-1 -mr-2 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              aria-label="Закрыть"
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-border px-6 py-3.5">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
