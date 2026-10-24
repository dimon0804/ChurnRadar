import { ArrowDownToLine, Send } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

type Mode = 'push' | 'pull'

const OPTIONS: { value: Mode; title: string; hint: string; icon: ReactNode }[] = [
  { value: 'push', title: 'Сам отправляет изменения', hint: 'push: симулятор шлёт события в ingest', icon: <Send /> },
  {
    value: 'pull',
    title: 'Только отдаёт API',
    hint: 'pull: данные забирает коннектор «биллинг»',
    icon: <ArrowDownToLine />,
  },
]

/** Режим симулятора: шлёт изменения сам или только отдаёт их через демо-API биллинга. */
export function SimulatorMode({
  mode,
  disabled,
  onChange,
}: {
  mode: Mode | undefined
  disabled?: boolean
  onChange: (mode: Mode) => void
}) {
  const current = mode ?? 'push'
  return (
    <div className="space-y-2">
      <p className="text-sm" id="sim-mode-label">
        Как данные попадают в систему
      </p>
      <div role="radiogroup" aria-labelledby="sim-mode-label" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
        {OPTIONS.map((o) => {
          const checked = current === o.value
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-label={`${o.title} (${o.value})`}
              disabled={disabled}
              onClick={() => !checked && onChange(o.value)}
              className={cn(
                'flex min-w-0 items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60',
                '[&_svg]:mt-0.5 [&_svg]:size-4 [&_svg]:shrink-0',
                checked ? 'border-ring bg-ring/5 [&_svg]:text-ring' : 'border-border hover:bg-accent/40 [&_svg]:text-muted-foreground',
              )}
            >
              {o.icon}
              <span className="min-w-0">
                <span className="block text-[13px] font-medium">{o.title}</span>
                <span className="block text-xs text-muted-foreground">{o.hint}</span>
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
