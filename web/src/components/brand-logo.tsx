import { useId } from 'react'
import logoSvg from '@/assets/brand/logo.svg?raw'
import markSvg from '@/assets/brand/logo-mark.svg?raw'
import { cn } from '@/lib/utils'

/**
 * Готовит SVG к вставке в разметку: у каждого экземпляра свои id масок и градиентов
 * (иначе две копии на странице ссылаются на одну, а спрятанная копия ломает видимую),
 * без собственных размеров, стилей и подписи — цвет и размер задаёт страница.
 */
function prepare(svg: string, prefix: string): string {
  return svg
    .replace(/<title>.*?<\/title>/, '')
    .replace(/<style>.*?<\/style>/, '')
    .replace(/ (width|height)="[\d.]+"/g, '')
    .replace(/ role="img" aria-label="[^"]*"/, ' aria-hidden="true" focusable="false"')
    .replace(/id="([^"]+)"/g, `id="${prefix}$1"`)
    .replace(/url\(#([^)]+)\)/g, `url(#${prefix}$1)`)
    .replace(/xlink:href="#([^"]+)"/g, `xlink:href="#${prefix}$1"`)
}

const cache = new Map<string, string>()
function prepared(svg: string, prefix: string) {
  const key = `${prefix}|${svg.length}`
  let out = cache.get(key)
  if (!out) {
    out = prepare(svg, prefix)
    cache.set(key, out)
  }
  return out
}

/**
 * Логотип ChurnRadar. variant="full" — пульс, «ChurnRadár» и радар (по ширине 3,44 высоты),
 * variant="mark" — квадратный знак для узких мест. Белые части берут currentColor,
 * «Churn» и пульс знака — цвет --logo-accent.
 */
export function BrandLogo({ variant = 'full', className }: { variant?: 'full' | 'mark'; className?: string }) {
  const prefix = `cr${useId().replace(/[^a-zA-Z0-9]/g, '')}-`
  const svg = variant === 'full' ? logoSvg : markSvg
  return (
    <span
      className={cn('cr-logo inline-block shrink-0 [&>svg]:block [&>svg]:size-full', variant === 'full' ? 'aspect-[898/261]' : 'aspect-square', className)}
      dangerouslySetInnerHTML={{ __html: prepared(svg, prefix) }}
    />
  )
}
