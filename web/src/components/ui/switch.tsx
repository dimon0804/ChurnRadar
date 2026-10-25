import * as SwitchPrimitive from '@radix-ui/react-switch'
import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

export function Switch({ className, ...props }: ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        'peer inline-flex h-6 w-[42px] shrink-0 items-center rounded-full border border-transparent bg-input transition-colors duration-200 outline-none',
        'focus-visible:ring-[3px] focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-brand',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block size-[18px] translate-x-[2px] rounded-full bg-white shadow-sm transition-transform duration-200 data-[state=checked]:translate-x-[20px]" />
    </SwitchPrimitive.Root>
  )
}
