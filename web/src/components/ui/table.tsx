import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

export function Table({ className, wrapperClassName, ...props }: ComponentProps<'table'> & { wrapperClassName?: string }) {
  return (
    <div className={cn('relative w-full overflow-x-auto', wrapperClassName)}>
      <table className={cn('w-full caption-bottom border-collapse text-sm', className)} {...props} />
    </div>
  )
}

export function TableHeader({ className, ...props }: ComponentProps<'thead'>) {
  return <thead className={cn('bg-surface-2 [&_tr]:border-b', className)} {...props} />
}

export function TableBody({ className, ...props }: ComponentProps<'tbody'>) {
  return <tbody className={cn('[&_tr:last-child]:border-0', className)} {...props} />
}

export function TableRow({ className, ...props }: ComponentProps<'tr'>) {
  return <tr className={cn('border-b border-border transition-colors duration-150 [tbody>&]:hover:bg-brand-soft', className)} {...props} />
}

export function TableHead({ className, ...props }: ComponentProps<'th'>) {
  return (
    <th
      className={cn(
        'h-11 bg-surface-2 px-3 text-left align-middle text-xs font-semibold whitespace-nowrap text-muted-foreground first:pl-[17px] last:pr-[17px]',
        className,
      )}
      {...props}
    />
  )
}

export function TableCell({ className, ...props }: ComponentProps<'td'>) {
  return <td className={cn('px-3 py-3 align-middle first:pl-[17px] last:pr-[17px]', className)} {...props} />
}
