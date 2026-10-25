import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

export const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md border border-transparent text-[13px] font-bold transition-[background-color,border-color,color,box-shadow,transform] duration-200 select-none outline-none focus-visible:ring-[3px] focus-visible:ring-ring/35 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'border-brand bg-brand font-extrabold text-brand-foreground hover:bg-brand/88 hover:shadow-[0_8px_22px_color-mix(in_srgb,var(--brand)_22%,transparent)]',
        secondary: 'border-border bg-surface-2 text-secondary-foreground hover:border-brand/60 hover:bg-brand-soft',
        outline: 'border-border bg-surface-2 text-foreground hover:border-brand/60 hover:bg-brand-soft',
        ghost: 'text-foreground hover:bg-accent',
        destructive: 'border-destructive bg-destructive font-extrabold text-destructive-foreground hover:bg-destructive/90',
        link: 'text-link underline-offset-4 hover:underline active:scale-100',
      },
      size: {
        default: 'h-10 px-3.5',
        sm: 'h-8 rounded-[9px] px-3 text-xs',
        lg: 'h-11 px-5 text-sm',
        icon: 'size-10 rounded-[11px]',
        'icon-sm': 'size-8 rounded-[9px]',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export function Button({
  className,
  variant,
  size,
  asChild = false,
  type,
  ...props
}: ComponentProps<'button'> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'button'
  return (
    <Comp
      className={cn(buttonVariants({ variant, size }), className)}
      type={asChild ? undefined : (type ?? 'button')}
      {...props}
    />
  )
}
