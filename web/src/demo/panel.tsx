import { ArrowUpRight, Check, Loader2, Lock, Play, RotateCcw, X } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { DemoStatus } from './demo-mode'
import { FEATURES, isExternal, type Feature } from './features'
import { ADMIN_ONLY_HINT, DEMO_STEPS, isLocked, type DemoStep } from './steps'

export type DemoTab = 'scenario' | 'features'

const TABS: { id: DemoTab; label: string }[] = [
  { id: 'scenario', label: 'Сценарий' },
  { id: 'features', label: 'Что реализовано' },
]

function StepItem({
  step,
  index,
  active,
  locked,
  onRun,
}: {
  step: DemoStep
  index: number
  active: boolean
  locked: boolean
  onRun: () => void
}) {
  return (
    <li
      className={cn(
        'rounded-lg border px-3.5 py-3 transition-colors',
        active ? 'border-foreground/25 bg-accent/60' : 'border-border bg-card',
      )}
      data-testid={`demo-step-${step.id}`}
    >
      <div className="flex items-center gap-3">
        <span
          className={cn(
            'flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold num',
            active ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground',
          )}
        >
          {index + 1}
        </span>
        <p className="min-w-0 flex-1 text-sm leading-5 font-medium">{step.title}</p>
        <Button
          size="sm"
          variant={active ? 'default' : 'outline'}
          className="h-7 shrink-0 px-2.5"
          disabled={locked}
          onClick={onRun}
          aria-label={`Показать: ${step.title}`}
        >
          {locked ? <Lock /> : <Play />}
          Показать
        </Button>
      </div>
      <p className="mt-1.5 pl-9 text-[13px] leading-5 text-muted-foreground">{step.say}</p>
      {locked && <p className="mt-1.5 pl-9 text-xs leading-5 text-risk-medium">{ADMIN_ONLY_HINT}</p>}
    </li>
  )
}

function FeatureItem({ feature, isAdmin, onShow }: { feature: Feature; isAdmin: boolean; onShow: () => void }) {
  const target = feature.target
  const step = target.kind === 'step' ? DEMO_STEPS.find((s) => s.id === target.step) : undefined
  const locked = (step && isLocked(step, isAdmin)) || (target.kind === 'route' && isLocked(target, isAdmin))
  const linkClass =
    'inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[13px] font-medium text-link outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring'

  return (
    <li className="flex items-start gap-3 py-2.5" data-testid={`demo-feature-${feature.id}`}>
      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-risk-low-bg text-risk-low">
        <Check className="size-3" strokeWidth={3} aria-label="реализовано" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-5 font-medium">{feature.title}</p>
        <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{feature.detail}</p>
      </div>
      {target.kind === 'url' ? (
        <a
          href={target.href}
          target="_blank"
          rel="noreferrer"
          className={linkClass}
          aria-label={`Открыть: ${feature.title}`}
        >
          {isExternal(target.href) ? 'Открыть' : 'Показать'}
          <ArrowUpRight className="size-3.5" aria-hidden />
        </a>
      ) : locked ? (
        <span className="inline-flex h-7 shrink-0 items-center gap-1 px-2 text-[13px] text-subtle-foreground" title={ADMIN_ONLY_HINT}>
          <Lock className="size-3.5" aria-hidden />
          Админ
        </span>
      ) : (
        <button type="button" onClick={onShow} className={linkClass} aria-label={`Показать: ${feature.title}`}>
          Показать
        </button>
      )}
    </li>
  )
}

export function StatusLine({ status }: { status: DemoStatus }) {
  if (status.state === 'idle') return null
  if (status.state === 'running')
    return (
      <p className="flex items-center gap-1.5 text-[13px] text-muted-foreground" role="status">
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        {status.label}
      </p>
    )
  if (status.state === 'error')
    return (
      <p className="text-[13px] leading-5 text-risk-high" role="alert">
        {status.message}
      </p>
    )
  return status.message ? (
    <p className="text-[13px] leading-5 text-risk-low" role="status">
      {status.message}
    </p>
  ) : null
}

export function DemoPanel({
  tab,
  onTab,
  isAdmin,
  activeStep,
  status,
  onRunStep,
  onShowFeature,
  onRestore,
  onClose,
}: {
  tab: DemoTab
  onTab: (tab: DemoTab) => void
  isAdmin: boolean
  activeStep: string | null
  status: DemoStatus
  onRunStep: (step: DemoStep) => void
  onShowFeature: (feature: Feature) => void
  onRestore: () => void
  onClose: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const required = FEATURES.filter((f) => !f.extra)
  const extra = FEATURES.filter((f) => f.extra)

  return (
    <aside
      aria-label="Пульт демонстрации"
      data-testid="demo-panel"
      className="fixed top-3 right-3 bottom-3 z-[70] flex w-[min(440px,calc(100vw-24px))] animate-sheet-in flex-col overflow-hidden rounded-xl border border-border bg-card text-card-foreground shadow-pop"
    >
      <div className="flex items-start gap-3 border-b border-border px-5 pt-4 pb-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold tracking-[-0.01em]">Пульт демонстрации</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            <kbd className="font-sans">Shift+D</kbd> открыть или скрыть, <kbd className="font-sans">Esc</kbd> закрыть
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="-mr-2 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Закрыть пульт"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="px-5 pt-3">
        <div className="inline-flex w-full rounded-md border border-border p-0.5" role="tablist" aria-label="Разделы пульта">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => onTab(t.id)}
              className={cn(
                'h-7 flex-1 rounded-[5px] text-[13px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring',
                tab === t.id ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3" role="tabpanel">
        {tab === 'scenario' ? (
          <>
            <p className="mb-3 text-[13px] leading-5 text-muted-foreground">
              Нажмите «Показать»: панель сама откроет нужную страницу и подсветит, куда смотреть. Текст шага — что сказать экспертам.
            </p>
            <ol className="space-y-2">
              {DEMO_STEPS.map((step, i) => (
                <StepItem
                  key={step.id}
                  step={step}
                  index={i}
                  active={activeStep === step.id}
                  locked={isLocked(step, isAdmin)}
                  onRun={() => onRunStep(step)}
                />
              ))}
            </ol>
          </>
        ) : (
          <>
            <h3 className="mt-1 text-[13px] font-semibold tracking-wide text-muted-foreground uppercase">Требования кейса</h3>
            <ul className="divide-y divide-border">
              {required.map((f) => (
                <FeatureItem key={f.id} feature={f} isAdmin={isAdmin} onShow={() => onShowFeature(f)} />
              ))}
            </ul>
            <h3 className="mt-5 text-[13px] font-semibold tracking-wide text-muted-foreground uppercase">Сверх кейса</h3>
            <ul className="divide-y divide-border">
              {extra.map((f) => (
                <FeatureItem key={f.id} feature={f} isAdmin={isAdmin} onShow={() => onShowFeature(f)} />
              ))}
            </ul>
          </>
        )}
      </div>

      <div className="space-y-2 border-t border-border bg-muted/40 px-5 py-3">
        <StatusLine status={status} />
        {isAdmin ? (
          confirming ? (
            <div className="flex items-center gap-2">
              <p className="min-w-0 flex-1 text-[13px] leading-5">Остановить поток, вернуть данные кейса и сбросить коэффициенты?</p>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                Нет
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  setConfirming(false)
                  onRestore()
                }}
              >
                Вернуть
              </Button>
            </div>
          ) : (
            <Button size="sm" variant="outline" className="w-full" onClick={() => setConfirming(true)} disabled={status.state === 'running'}>
              <RotateCcw />
              Вернуть стенд в исходное состояние
            </Button>
          )
        ) : (
          <p className="text-xs leading-5 text-muted-foreground">Вы вошли как аналитик: шаги с изменением модели и данных доступны только администратору.</p>
        )}
      </div>
    </aside>
  )
}

