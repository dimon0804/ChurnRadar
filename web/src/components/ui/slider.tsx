import * as SliderPrimitive from '@radix-ui/react-slider'
import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

export function Slider({
  className,
  thumbLabels,
  rangeClassName,
  trackClassName,
  ...props
}: ComponentProps<typeof SliderPrimitive.Root> & {
  thumbLabels?: string[]
  rangeClassName?: string
  trackClassName?: string
}) {
  const count = (props.value ?? props.defaultValue ?? [0]).length
  return (
    <SliderPrimitive.Root
      className={cn(
        'relative flex h-5 w-full touch-none items-center select-none data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    >
      <SliderPrimitive.Track className={cn('relative h-1.5 w-full grow overflow-hidden rounded-full bg-muted', trackClassName)}>
        <SliderPrimitive.Range className={cn('absolute h-full bg-primary/80', rangeClassName)} />
      </SliderPrimitive.Track>
      {Array.from({ length: count }, (_, i) => (
        <SliderPrimitive.Thumb
          key={i}
          aria-label={thumbLabels?.[i]}
          className="block size-4 rounded-full border-2 border-primary bg-card shadow-sm transition-[box-shadow,transform] duration-100 outline-none hover:scale-110 focus-visible:ring-4 focus-visible:ring-ring/30 data-[disabled]:pointer-events-none"
        />
      ))}
    </SliderPrimitive.Root>
  )
}
