import { cn } from '@/lib/utils'

/**
 * Короткая подсветка поверх блока после пересчёта. Рисуется слоем, чтобы не перемонтировать
 * содержимое (иначе числа перестанут плавно докручиваться). Родителю нужен position: relative.
 */
export function FlashOverlay({ className }: { className?: string }) {
  return <span aria-hidden className={cn('pointer-events-none absolute inset-0 rounded-[inherit] animate-value-flash', className)} />
}
