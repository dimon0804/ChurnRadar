import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ComponentProps, ReactNode } from 'react'
import { cn } from '@/lib/utils'

export const Sheet = DialogPrimitive.Root
export const SheetTitle = DialogPrimitive.Title
export const SheetDescription = DialogPrimitive.Description

export function SheetContent({
  className,
  children,
  closeLabel = 'Закрыть',
  header,
  ...props
}: ComponentProps<typeof DialogPrimitive.Content> & { closeLabel?: string; header?: ReactNode }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-[var(--overlay)] backdrop-blur-[7px] data-[state=closed]:animate-overlay-out data-[state=open]:animate-overlay-in" />
      <DialogPrimitive.Content
        className={cn(
          'fixed inset-y-0 right-0 z-50 flex w-full max-w-[min(840px,100vw)] flex-col border-l border-border bg-background shadow-pop outline-none',
          'data-[state=closed]:animate-sheet-out data-[state=open]:animate-sheet-in',
          className,
        )}
        {...props}
      >
        <div className="flex items-start gap-3 border-b border-border bg-card px-4 py-4 sm:px-[23px] sm:py-5">
          <div className="min-w-0 flex-1">{header}</div>
          <DialogPrimitive.Close
            className="-mr-1 inline-flex size-9 shrink-0 items-center justify-center rounded-[11px] border border-border bg-card text-muted-foreground transition-colors hover:border-brand/60 hover:bg-brand-soft hover:text-foreground"
            aria-label={closeLabel}
          >
            <X className="size-4" />
          </DialogPrimitive.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
}
