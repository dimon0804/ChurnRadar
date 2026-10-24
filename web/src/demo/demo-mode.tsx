import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router'
import { errorMessage } from '@/api/client'
import { useAuth } from '@/providers/auth'
import { CueCard } from './cue-card'
import { demoApi } from './demo-api'
import { FEATURES, type Feature } from './features'
import { hasDemoParam, isDemoHotkey } from './hotkey'
import { DemoPanel, type DemoTab } from './panel'
import { sleep, waitForTarget, type Target } from './spotlight'
import { SpotlightLayer } from './spotlight-layer'
import { DEMO_STEPS, isLocked, type DemoContext, type DemoResult, type DemoStep } from './steps'
import { readStored, writeStored } from './storage'

export type DemoMode = 'closed' | 'panel' | 'cue'
export type Cue = { kind: 'step'; id: string } | { kind: 'feature'; id: string }
export type DemoStatus =
  | { state: 'idle' }
  | { state: 'running'; label: string }
  | { state: 'done'; message?: string }
  | { state: 'error'; message: string }

const OPEN_DIALOG = '[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]'

function initialTab(): DemoTab {
  return readStored('tab') === 'features' ? 'features' : 'scenario'
}

/** Клик по пульту не должен закрывать открытую карточку клиента. */
const keepLayersOpen = (event: PointerEvent) => event.stopPropagation()

export function DemoMode() {
  const { isAdmin } = useAuth()
  const navigate = useNavigate()
  const fromUrl = useMemo(() => hasDemoParam(window.location.search), [])
  const [mode, setMode] = useState<DemoMode>(fromUrl ? 'panel' : 'closed')
  const [seen, setSeen] = useState(() => fromUrl || readStored('seen') === '1')
  const [tab, setTab] = useState<DemoTab>(initialTab)
  const [cue, setCue] = useState<Cue | null>(null)
  const [status, setStatus] = useState<DemoStatus>({ state: 'idle' })
  const [spot, setSpot] = useState<{ targets: Target[]; nonce: number }>({ targets: [], nonce: 0 })
  const runId = useRef(0)

  useEffect(() => {
    if (fromUrl) writeStored('seen', '1')
  }, [fromUrl])

  const show = useCallback((next: DemoMode) => {
    setMode(next)
    if (next !== 'closed') {
      setSeen(true)
      writeStored('seen', '1')
    }
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isDemoHotkey(event)) {
        event.preventDefault()
        setMode((m) => (m === 'panel' ? 'closed' : 'panel'))
        setSeen(true)
        writeStored('seen', '1')
        return
      }
      if (event.key === 'Escape' && !event.defaultPrevented && !document.querySelector(OPEN_DIALOG)) {
        setMode('closed')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const changeTab = (next: DemoTab) => {
    setTab(next)
    writeStored('tab', next)
  }

  const ctx = useMemo<DemoContext>(
    () => ({
      isAdmin,
      api: demoApi,
      navigate: (to) => void navigate(to),
      spotlight: (targets) => setSpot((prev) => ({ targets, nonce: prev.nonce + 1 })),
      waitFor: (target) => waitForTarget(target),
      wait: sleep,
      redirect: (url) => window.location.assign(url),
    }),
    [isAdmin, navigate],
  )

  const perform = useCallback(
    async (label: string, fn: (c: DemoContext) => Promise<DemoResult>) => {
      const id = ++runId.current
      setStatus({ state: 'running', label })
      try {
        const message = await fn(ctx)
        if (id === runId.current) setStatus({ state: 'done', message: message || undefined })
      } catch (error) {
        if (id === runId.current) setStatus({ state: 'error', message: errorMessage(error) })
      }
    },
    [ctx],
  )

  const runStep = useCallback(
    (step: DemoStep) => {
      if (isLocked(step, isAdmin)) return
      setCue({ kind: 'step', id: step.id })
      show('cue')
      if (step.run) void perform('Показываю…', step.run)
      else {
        runId.current += 1
        setStatus({ state: 'idle' })
      }
    },
    [isAdmin, perform, show],
  )

  const showFeature = useCallback(
    (feature: Feature) => {
      const target = feature.target
      if (target.kind === 'step') {
        const step = DEMO_STEPS.find((s) => s.id === target.step)
        if (step) runStep(step)
        return
      }
      if (target.kind !== 'route') return
      runId.current += 1
      setStatus({ state: 'idle' })
      setCue({ kind: 'feature', id: feature.id })
      show('cue')
      navigate(target.to)
      if (target.targets) ctx.spotlight(target.targets)
    },
    [ctx, navigate, runStep, show],
  )

  const restoreAll = useCallback(
    () =>
      perform('Возвращаю стенд…', async (c) => {
        await c.api.simulator({ running: false })
        await c.api.restoreCaseData()
        await c.api.resetSettings()
        c.navigate('/')
        return 'Готово: данные кейса, поток остановлен, коэффициенты по умолчанию.'
      }),
    [perform],
  )

  const cueStep = cue?.kind === 'step' ? DEMO_STEPS.find((s) => s.id === cue.id) : undefined
  const cueFeature = cue?.kind === 'feature' ? FEATURES.find((f) => f.id === cue.id) : undefined

  return createPortal(
    <div data-demo-root>
      <SpotlightLayer targets={spot.targets} nonce={spot.nonce} />

      {mode === 'panel' && (
        <div className="pointer-events-auto" onPointerDown={keepLayersOpen}>
          <DemoPanel
            tab={tab}
            onTab={changeTab}
            isAdmin={isAdmin}
            activeStep={cueStep?.id ?? null}
            status={status}
            onRunStep={runStep}
            onShowFeature={showFeature}
            onRestore={restoreAll}
            onClose={() => setMode('closed')}
          />
        </div>
      )}

      {mode === 'cue' && (cueStep || cueFeature) && (
        <div className="pointer-events-auto" onPointerDown={keepLayersOpen}>
          <CueCard
            step={cueStep}
            feature={cueFeature}
            isAdmin={isAdmin}
            status={status}
            onRunStep={runStep}
            onAction={(action) => void perform(`${action.label}…`, action.run)}
            onOpenPanel={() => show('panel')}
            onClose={() => setMode('closed')}
          />
        </div>
      )}

      {mode === 'closed' && seen && (
        <button
          type="button"
          onClick={() => show(cue ? 'cue' : 'panel')}
          onPointerDown={keepLayersOpen}
          className="pointer-events-auto fixed right-4 bottom-4 z-[60] inline-flex h-7 items-center gap-1.5 rounded-full border border-border bg-card/90 px-2.5 text-[11px] font-medium text-muted-foreground shadow-card backdrop-blur-md transition-colors outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Открыть пульт демонстрации (Shift+D)"
          data-testid="demo-hint"
        >
          <kbd className="rounded border border-border bg-muted px-1 font-sans text-[10px] leading-4">Shift+D</kbd>
          демо
        </button>
      )}
    </div>,
    document.body,
  )
}
