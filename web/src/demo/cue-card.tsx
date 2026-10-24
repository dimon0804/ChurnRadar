import { ArrowLeft, ArrowRight, ArrowUpRight, Lock, MessageSquareQuote, PanelRightOpen, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { DemoStatus } from './demo-mode'
import type { Feature } from './features'
import { StatusLine } from './panel'
import { ReadoutView } from './readouts'
import { ADMIN_ONLY_HINT, DEMO_STEPS, isLocked, type DemoAction, type DemoStep } from './steps'

function nextUnlocked(from: number, dir: 1 | -1, isAdmin: boolean): DemoStep | undefined {
  for (let i = from + dir; i >= 0 && i < DEMO_STEPS.length; i += dir) {
    const step = DEMO_STEPS[i]
    if (step && !isLocked(step, isAdmin)) return step
  }
  return undefined
}

/** Подсказка ведущему: что сказать и что показать, пока эксперты смотрят на экран. */
export function CueCard({
  step,
  feature,
  isAdmin,
  status,
  onRunStep,
  onAction,
  onOpenPanel,
  onClose,
}: {
  step?: DemoStep
  feature?: Feature
  isAdmin: boolean
  status: DemoStatus
  onRunStep: (step: DemoStep) => void
  onAction: (action: DemoAction) => void
  onOpenPanel: () => void
  onClose: () => void
}) {
  const index = step ? DEMO_STEPS.indexOf(step) : -1
  const prev = step ? nextUnlocked(index, -1, isAdmin) : undefined
  const next = step ? nextUnlocked(index, 1, isAdmin) : undefined
  const busy = status.state === 'running'

  return (
    <section
      aria-label="Подсказка к шагу демонстрации"
      data-testid="demo-cue"
      className="fixed bottom-4 left-4 z-[70] flex max-h-[calc(100vh-88px)] w-[min(400px,calc(100vw-32px))] animate-pop-in flex-col overflow-hidden rounded-xl border border-border bg-card text-card-foreground shadow-pop"
    >
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <p className="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground num">
          {step ? `Шаг ${index + 1} из ${DEMO_STEPS.length}` : feature?.extra ? 'Сверх кейса' : 'Требование кейса'}
        </p>
        <button
          type="button"
          onClick={onOpenPanel}
          className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          <PanelRightOpen className="size-3.5" aria-hidden />
          Пульт
        </button>
        <button
          type="button"
          onClick={onClose}
          className="-mr-1.5 inline-flex size-7 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Скрыть подсказку"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="min-h-0 space-y-3 overflow-y-auto px-4 py-3">
        <h2 className="text-[15px] leading-5 font-semibold tracking-[-0.01em]">{step?.title ?? feature?.title}</h2>

        {step ? (
          <>
            <div className="rounded-lg bg-muted/70 px-3 py-2.5">
              <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
                <MessageSquareQuote className="size-3.5" aria-hidden />
                Что сказать
              </p>
              <p className="text-[13px] leading-5">{step.say}</p>
            </div>
            <p className="text-[13px] leading-5 text-muted-foreground">
              <span className="font-medium text-foreground">Что показать: </span>
              {step.show}
            </p>
          </>
        ) : (
          <p className="text-[13px] leading-5 text-muted-foreground">{feature?.detail}</p>
        )}

        <StatusLine status={status} />

        {step?.readout && <ReadoutView kind={step.readout} />}

        {step?.links && (
          <ul className="space-y-1">
            {step.links.map((link) => (
              <li key={link.href}>
                <a
                  href={link.href}
                  target="_blank"
                  rel="noreferrer"
                  className="group flex items-baseline gap-2 rounded-md px-2 py-1.5 outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="inline-flex items-center gap-1 text-sm font-medium text-link">
                    {link.label}
                    <ArrowUpRight className="size-3.5" aria-hidden />
                  </span>
                  <span className="min-w-0 truncate text-xs text-muted-foreground">{link.hint}</span>
                </a>
              </li>
            ))}
          </ul>
        )}

        {step?.actions && (
          <div className="flex flex-wrap gap-1.5">
            {step.actions.map((action) => {
              const locked = isLocked(action, isAdmin)
              return (
                <Button
                  key={action.id}
                  size="sm"
                  variant="outline"
                  disabled={locked || busy}
                  title={locked ? ADMIN_ONLY_HINT : undefined}
                  onClick={() => onAction(action)}
                >
                  {locked && <Lock />}
                  {action.label}
                </Button>
              )
            })}
          </div>
        )}
      </div>

      {step && (
        <div className="flex items-center justify-between gap-2 border-t border-border bg-muted/40 px-4 py-2.5">
          <Button size="sm" variant="ghost" disabled={!prev || busy} onClick={() => prev && onRunStep(prev)}>
            <ArrowLeft />
            Назад
          </Button>
          {next ? (
            <Button size="sm" disabled={busy} onClick={() => onRunStep(next)} aria-label={`Дальше: ${next.title}`}>
              Дальше
              <ArrowRight />
            </Button>
          ) : (
            <Button size="sm" variant="outline" onClick={onOpenPanel}>
              К пульту
            </Button>
          )}
        </div>
      )}
    </section>
  )
}
