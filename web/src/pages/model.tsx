import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, History, Loader2, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { FeatureWeight, ModelSettings, ModelSettingsPatch, SettingsChange, Summary } from '@/api/types'
import { AnimatedNumber } from '@/components/animated-number'
import { FlashOverlay } from '@/components/flash'
import { PageHeader } from '@/components/page-header'
import { EmptyState, ErrorState } from '@/components/states'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { useSettingsPreview, WhatIfBar, WhatIfChanges } from '@/components/what-if'
import { useChangeStamp, usePreviousDistinct } from '@/hooks/use-change-stamp'
import { useNow } from '@/hooks/use-now'
import { formatAgo, formatDateTime, formatDecimal, formatDuration, formatInt, formatMoney, formatShare, NBSP, plural } from '@/lib/format'
import { groupLabel } from '@/lib/labels'
import { applyPatch, clamp, groupWeights, isEmptyPatch, mergePatch, weightStep } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { useLive } from '@/providers/live'

const DEBOUNCE_MS = 400

const plainNumber = (v: number) => formatDecimal(v, 1).split(NBSP).join('')
const HORIZON_PRESETS = [7, 14, 30, 60, 90]

/** Числовое поле, которое не мешает печатать: значение применяется при валидном вводе, на blur — зажимается. */
function NumberField({
  value,
  min,
  max,
  step,
  onCommit,
  ariaLabel,
  className,
  suffix,
  disabled,
}: {
  value: number
  min: number
  max: number
  step: number
  onCommit: (v: number) => void
  ariaLabel: string
  className?: string
  suffix?: string
  disabled?: boolean
}) {
  const [text, setText] = useState(plainNumber(value))
  const [focused, setFocused] = useState(false)
  const shown = focused ? text : plainNumber(value)

  const parse = (raw: string) => Number(raw.replace(',', '.').replace(/\s/g, ''))

  return (
    <div className={cn('relative', className)}>
      <Input
        inputMode="decimal"
        aria-label={ariaLabel}
        value={shown}
        disabled={disabled}
        onFocus={() => {
          setText(plainNumber(value))
          setFocused(true)
        }}
        onChange={(e) => {
          setText(e.target.value)
          const v = parse(e.target.value)
          if (e.target.value.trim() !== '' && Number.isFinite(v) && v >= min && v <= max) onCommit(v)
        }}
        onBlur={() => {
          setFocused(false)
          const v = parse(text)
          if (Number.isFinite(v) && text.trim() !== '') {
            const c = clamp(Math.round(v / step) * step, min, max)
            if (c !== value) onCommit(Number(c.toFixed(2)))
          }
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault()
            const next = clamp(value + (e.key === 'ArrowUp' ? step : -step), min, max)
            onCommit(Number(next.toFixed(2)))
            setText(plainNumber(next))
          }
        }}
        className={cn('h-8 text-right num', suffix && 'pr-9')}
      />
      {suffix && <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-xs text-muted-foreground">{suffix}</span>}
    </div>
  )
}

function WeightRow({ w, onChange }: { w: FeatureWeight; onChange: (patch: { weight?: number; enabled?: boolean }) => void }) {
  const titleId = useId()
  const step = weightStep(w)
  const isDefault = typeof w.default_weight === 'number' && Math.abs(w.default_weight - w.weight) < step / 2
  return (
    <li className={cn('grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 py-3.5 md:grid-cols-[auto_minmax(0,1fr)_minmax(180px,240px)_84px] md:items-center')}>
      <Switch checked={w.enabled} onCheckedChange={(enabled) => onChange({ enabled })} aria-labelledby={titleId} className="mt-0.5 md:mt-0" />
      <div className={cn('min-w-0 transition-opacity', !w.enabled && 'opacity-55')}>
        <p id={titleId} className="text-sm font-medium">
          {w.title}
        </p>
        {w.description && <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{w.description}</p>}
        {typeof w.default_weight === 'number' && !isDefault && (
          <button
            type="button"
            onClick={() => onChange({ weight: w.default_weight })}
            className="mt-1 rounded text-xs text-link outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
          >
            Вернуть {formatDecimal(w.default_weight, 1)} по умолчанию
          </button>
        )}
      </div>
      <div className={cn('col-start-2 md:col-start-auto', !w.enabled && 'opacity-55')}>
        <Slider
          value={[w.weight]}
          min={w.min}
          max={w.max}
          step={step}
          disabled={!w.enabled}
          onValueChange={([v]) => v !== undefined && onChange({ weight: v })}
          thumbLabels={[`Вес: ${w.title}`]}
        />
      </div>
      <NumberField
        className="col-start-2 w-24 md:col-start-auto md:w-auto"
        value={w.weight}
        min={w.min}
        max={w.max}
        step={step}
        disabled={!w.enabled}
        ariaLabel={`Вес: ${w.title}`}
        onCommit={(weight) => onChange({ weight })}
      />
    </li>
  )
}

function ThresholdsCard({ s, onChange }: { s: ModelSettings; onChange: (t: { medium?: number; high?: number }) => void }) {
  const { medium, high } = s.thresholds
  return (
    <Card>
      <CardHeader>
        <CardTitle>Пороги уровней</CardTitle>
        <CardDescription>С какого score клиент считается в зоне среднего и высокого риска.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="relative pt-1">
          <Slider
            value={[medium, high]}
            min={0}
            max={100}
            step={1}
            minStepsBetweenThumbs={1}
            onValueChange={([m, h]) => {
              if (m === undefined || h === undefined) return
              onChange({ medium: m, high: h })
            }}
            thumbLabels={['Порог среднего риска', 'Порог высокого риска']}
            trackClassName="h-2"
            rangeClassName="bg-risk-medium-bar"
          />
          <div className="pointer-events-none absolute inset-x-0 top-[9px] h-2 overflow-hidden rounded-full" aria-hidden>
            <div className="absolute inset-y-0 left-0 bg-risk-low-bar/80" style={{ width: `${medium}%` }} />
            <div className="absolute inset-y-0 right-0 bg-risk-high-bar/80" style={{ width: `${100 - high}%` }} />
          </div>
        </div>
        <div className="mt-2 flex justify-between text-[11px] text-subtle-foreground num">
          <span>0</span>
          <span>100</span>
        </div>
        <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs">
          <div className="rounded-md bg-risk-low-bg px-2 py-2 text-risk-low">
            <p className="font-medium">Низкий</p>
            <p className="mt-0.5 num">0–{Math.max(0, medium - 1)}</p>
          </div>
          <div className="rounded-md bg-risk-medium-bg px-2 py-2 text-risk-medium">
            <p className="font-medium">Средний</p>
            <p className="mt-0.5 num">
              {medium}–{Math.max(medium, high - 1)}
            </p>
          </div>
          <div className="rounded-md bg-risk-high-bg px-2 py-2 text-risk-high">
            <p className="font-medium">Высокий</p>
            <p className="mt-0.5 num">{high}–100</p>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3">
          <label className="space-y-1.5">
            <span className="text-xs text-muted-foreground">Средний от</span>
            <NumberField value={medium} min={0} max={Math.max(0, high - 1)} step={1} ariaLabel="Порог среднего риска" onCommit={(v) => onChange({ medium: v })} />
          </label>
          <label className="space-y-1.5">
            <span className="text-xs text-muted-foreground">Высокий от</span>
            <NumberField value={high} min={Math.min(100, medium + 1)} max={100} step={1} ariaLabel="Порог высокого риска" onCommit={(v) => onChange({ high: v })} />
          </label>
        </div>
      </CardContent>
    </Card>
  )
}

function HorizonCard({ s, onChange }: { s: ModelSettings; onChange: (days: number) => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Горизонт прогноза</CardTitle>
        <CardDescription>За какой период считаем ожидаемую потерю и прогноз непродлений.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-3">
          <NumberField value={s.horizon_days} min={1} max={365} step={1} ariaLabel="Горизонт прогноза, дней" suffix="дн." onCommit={(v) => onChange(Math.round(v))} className="w-28" />
          <span className="text-[13px] text-muted-foreground">от 1 до 365 дней</span>
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Быстрый выбор горизонта">
          {HORIZON_PRESETS.map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={s.horizon_days === d}
              onClick={() => onChange(d)}
              className={cn(
                'h-7 rounded-full border px-2.5 text-xs font-medium transition-colors outline-none num focus-visible:ring-2 focus-visible:ring-ring',
                s.horizon_days === d ? 'border-primary bg-primary text-primary-foreground' : 'border-border text-muted-foreground hover:text-foreground',
              )}
            >
              {d} {plural(d, ['день', 'дня', 'дней'])}
            </button>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function describeChange(change: SettingsChange['changes'][number], titles: Map<string, string>): string {
  const fmt = (v: unknown) =>
    typeof v === 'number' ? formatDecimal(v, 1) : typeof v === 'boolean' ? (v ? 'вкл.' : 'выкл.') : v === null || v === undefined ? '—' : String(v)
  const parts = change.path.split('.')
  if (parts[0] === 'weights' && parts.length >= 3) {
    const code = parts.slice(1, -1).join('.')
    const field = parts.at(-1)
    const title = titles.get(code) ?? code
    if (field === 'enabled') return `«${title}» ${change.new ? 'включён' : 'выключен'}`
    return `«${title}»: ${fmt(change.old)} → ${fmt(change.new)}`
  }
  if (change.path === 'thresholds.medium') return `Порог среднего риска: ${fmt(change.old)} → ${fmt(change.new)}`
  if (change.path === 'thresholds.high') return `Порог высокого риска: ${fmt(change.old)} → ${fmt(change.new)}`
  if (change.path === 'horizon_days') return `Горизонт прогноза: ${fmt(change.old)} → ${fmt(change.new)} дн.`
  if (change.path === 'reset' || change.path === '*') return 'Сброс к значениям по умолчанию'
  return `${change.path}: ${fmt(change.old)} → ${fmt(change.new)}`
}

function HistoryCard({ titles }: { titles: Map<string, string> }) {
  const history = useQuery({ queryKey: queryKeys.history, queryFn: ({ signal }) => api.settingsHistory(30, signal) })
  const items = history.data?.items ?? []
  return (
    <Card>
      <CardHeader>
        <CardTitle>История изменений</CardTitle>
        <CardDescription>Кто и что менял в модели</CardDescription>
      </CardHeader>
      <CardContent className="pt-2">
        {history.isPending ? (
          <div className="space-y-3 pt-2">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : history.isError ? (
          <ErrorState error={history.error} onRetry={() => void history.refetch()} className="py-6" />
        ) : items.length === 0 ? (
          <EmptyState icon={<History />} title="Изменений пока не было" className="py-6" />
        ) : (
          <ol className="max-h-[420px] overflow-y-auto pr-1 pl-1">
            {items.map((h) => (
              <li key={h.version} className="relative border-l border-border py-2 pl-4 before:absolute before:top-3.5 before:-left-[3.5px] before:size-1.5 before:rounded-full before:bg-subtle-foreground">
                <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground num">
                  <span className="font-medium text-foreground">v{h.version}</span>
                  <span>{formatDateTime(h.changed_at)}</span>
                  <span className="truncate">· {h.changed_by ?? 'система'}</span>
                </p>
                <ul className="mt-1 space-y-0.5">
                  {(h.changes ?? []).slice(0, 6).map((c, i) => (
                    <li key={i} className="text-[13px] leading-5 break-words">
                      {describeChange(c, titles)}
                    </li>
                  ))}
                  {(h.changes?.length ?? 0) > 6 && <li className="text-xs text-muted-foreground">и ещё {(h.changes?.length ?? 0) - 6}</li>}
                  {(h.changes?.length ?? 0) === 0 && <li className="text-[13px] text-muted-foreground">Без изменений значений</li>}
                </ul>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}

/** Одна итоговая цифра над редактором: значение, подсветка после пересчёта и сдвиг относительно прошлого. */
function ImpactStat({
  label,
  value,
  format,
  formatDelta,
  goodWhenDown = true,
  testId,
}: {
  label: string
  value: number | null
  format: (v: number | null | undefined) => string
  formatDelta: (delta: number) => string | null
  goodWhenDown?: boolean
  testId: string
}) {
  const stamp = useChangeStamp(value)
  const prev = usePreviousDistinct(value)
  const delta = value !== null && prev !== null ? value - prev : null
  const deltaText = delta !== null && delta !== 0 ? formatDelta(delta) : null
  const good = delta !== null && (goodWhenDown ? delta < 0 : delta > 0)
  return (
    <div className="relative min-w-0 rounded-lg border border-border bg-card px-3 py-2" data-testid={testId}>
      {stamp > 0 && <FlashOverlay key={stamp} />}
      <p className="line-clamp-2 text-xs text-muted-foreground sm:line-clamp-1" title={label}>{label}</p>
      <p className="mt-0.5 flex items-baseline gap-2">
        <span className="text-lg leading-6 font-semibold tracking-[-0.01em] num" data-role="value">
          <AnimatedNumber value={value} format={format} />
        </span>
        {deltaText && (
          <span
            className={cn('truncate text-xs font-medium num', good ? 'text-risk-low' : 'text-risk-high')}
            title={prev !== null ? `До последнего пересчёта: ${format(prev)}` : undefined}
          >
            {deltaText}
          </span>
        )}
      </p>
    </div>
  )
}

const signed = (text: string, delta: number) => `${delta > 0 ? '+' : '−'}${text}`

/** Что изменилось в итоге: три главные цифры обзора, обновляются по SSE сразу после пересчёта. */
function ImpactBar() {
  const summary = useQuery({ queryKey: queryKeys.summary, queryFn: ({ signal }) => api.summary(signal) })
  const s: Summary | undefined = summary.data
  if (!s) {
    return (
      <div className="grid grid-cols-3 gap-2">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-[58px] rounded-lg" />
        ))}
      </div>
    )
  }
  const share = typeof s.revenue_at_risk_share === 'number' ? s.revenue_at_risk_share : null
  return (
    <div className="grid grid-cols-3 gap-2" aria-label="Итог по всем клиентам" role="group">
      <ImpactStat
        label="Клиентов высокого риска"
        testId="impact-high"
        value={s.by_level.high}
        format={formatInt}
        formatDelta={(d) => signed(formatInt(Math.abs(d)), d)}
      />
      <ImpactStat
        label={`Ожидаемая потеря за ${s.horizon_days}${NBSP}дн.`}
        testId="impact-expected-loss"
        value={s.expected_loss}
        format={(v) => formatMoney(v, { cents: false })}
        formatDelta={(d) => (Math.abs(d) < 50 ? null : signed(formatMoney(Math.abs(d), { cents: false }), d))}
      />
      <ImpactStat
        label="Доля выручки под риском"
        testId="impact-share"
        value={share}
        format={(v) => formatShare(v)}
        formatDelta={(d) => (Math.abs(d) < 0.0005 ? null : `${d > 0 ? '+' : '−'}${formatDecimal(Math.abs(d) * 100, 1)}${NBSP}п.п.`)}
      />
    </div>
  )
}

type SaveState = { kind: 'idle' } | { kind: 'saving' } | { kind: 'error'; message: string }

function StatusLine({ save, settings, awaitingVersion, onRetry }: { save: SaveState; settings: ModelSettings; awaitingVersion: number | null; onRetry: () => void }) {
  const { lastRecalc, status } = useLive()
  const now = useNow(1000)
  const recalculating = awaitingVersion !== null && (!lastRecalc || lastRecalc.modelVersion < awaitingVersion)

  let icon = <CheckCircle2 className="size-4 text-risk-low" />
  let text: string
  if (save.kind === 'error') {
    icon = <AlertCircle className="size-4 text-risk-high" />
    text = `Не сохранилось: ${save.message}`
  } else if (save.kind === 'saving') {
    icon = <Loader2 className="size-4 animate-spin text-muted-foreground" />
    text = 'Сохраняем…'
  } else if (recalculating) {
    icon = <Loader2 className="size-4 animate-spin text-muted-foreground" />
    text = status === 'online' ? `Пересчитываем оценки · версия ${awaitingVersion}` : `Сохранено · версия ${awaitingVersion} · ждём связи для обновления`
  } else if (lastRecalc) {
    const took = lastRecalc.durationMs !== null ? ` за ${formatDuration(lastRecalc.durationMs)}` : ''
    text = `Пересчитано${took} · версия ${lastRecalc.modelVersion} · ${formatAgo(lastRecalc.receivedAt, now)}`
  } else {
    const t = new Date(settings.updated_at).getTime()
    text = `Действует версия ${settings.version}${Number.isNaN(t) ? '' : ` · изменена ${formatAgo(Math.min(t, now), now)}`}${settings.updated_by ? ` · ${settings.updated_by}` : ''}`
  }

  return (
    <div role="status" aria-live="polite" className="flex min-w-0 items-center gap-2 text-[13px] text-muted-foreground num">
      {icon}
      <span className="truncate">{text}</span>
      {save.kind === 'error' && (
        <Button variant="link" size="sm" className="h-auto px-1" onClick={onRetry}>
          Обновить
        </Button>
      )}
    </div>
  )
}

function ModelSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]" aria-busy="true" aria-label="Загружаем настройки">
      <div className="space-y-4">
        {Array.from({ length: 2 }, (_, i) => (
          <Skeleton key={i} className="h-72 rounded-xl" />
        ))}
      </div>
      <div className="space-y-4">
        <Skeleton className="h-64 rounded-xl" />
        <Skeleton className="h-40 rounded-xl" />
      </div>
    </div>
  )
}

export default function ModelPage() {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: ({ signal }) => api.settings(signal) })
  const [draft, setDraft] = useState<ModelSettings | null>(null)
  const [save, setSave] = useState<SaveState>({ kind: 'idle' })
  const [awaitingVersion, setAwaitingVersion] = useState<number | null>(null)
  const pending = useRef<ModelSettingsPatch>({})
  const inFlight = useRef(0)
  const timer = useRef<number | undefined>(undefined)
  // «Что если»: правки копятся здесь и не сохраняются до «Применить»
  const [whatIf, setWhatIf] = useState(false)
  const [whatIfPatch, setWhatIfPatch] = useState<ModelSettingsPatch>({})
  const preview = useSettingsPreview(whatIf ? whatIfPatch : null)

  const saved = draft ?? settings.data ?? null
  const current = whatIf && saved ? applyPatch(saved, whatIfPatch) : saved

  const mutation = useMutation({ mutationFn: api.patchSettings })
  const apply = useMutation({
    mutationFn: api.patchSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.settings, data)
      setAwaitingVersion((v) => Math.max(v ?? 0, data.version))
      setWhatIf(false)
      setWhatIfPatch({})
      setSave({ kind: 'idle' })
      void queryClient.invalidateQueries({ queryKey: queryKeys.history })
    },
    onError: (error) => setSave({ kind: 'error', message: errorMessage(error) }),
  })
  const reset = useMutation({
    mutationFn: api.resetSettings,
    onSuccess: (data) => {
      pending.current = {}
      setDraft(null)
      queryClient.setQueryData(queryKeys.settings, data)
      setAwaitingVersion(data.version)
      setSave({ kind: 'idle' })
      void queryClient.invalidateQueries({ queryKey: queryKeys.history })
    },
    onError: (error) => setSave({ kind: 'error', message: errorMessage(error) }),
  })

  const flush = useCallback(() => {
    const body = pending.current
    if (isEmptyPatch(body)) return
    pending.current = {}
    inFlight.current += 1
    setSave({ kind: 'saving' })
    mutation.mutate(body, {
      onSuccess: (data) => {
        inFlight.current -= 1
        queryClient.setQueryData(queryKeys.settings, data)
        setAwaitingVersion((v) => Math.max(v ?? 0, data.version))
        if (inFlight.current === 0 && isEmptyPatch(pending.current)) {
          setDraft(null)
          setSave({ kind: 'idle' })
        }
        void queryClient.invalidateQueries({ queryKey: queryKeys.history })
      },
      onError: (error) => {
        inFlight.current -= 1
        pending.current = {}
        setDraft(null)
        setSave({ kind: 'error', message: errorMessage(error) })
        void queryClient.invalidateQueries({ queryKey: queryKeys.settings })
      },
    })
  }, [mutation, queryClient])

  const change = useCallback(
    (patch: ModelSettingsPatch) => {
      if (whatIf) {
        setWhatIfPatch((p) => mergePatch(p, patch))
        return
      }
      const base = draft ?? settings.data
      if (!base) return
      setDraft(applyPatch(base, patch))
      pending.current = mergePatch(pending.current, patch)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(flush, DEBOUNCE_MS)
    },
    [whatIf, draft, settings.data, flush],
  )

  const toggleWhatIf = (on: boolean) => {
    if (on) {
      // несохранённая правка из обычного режима уходит сразу, «было» считается уже с ней
      window.clearTimeout(timer.current)
      flush()
    }
    setWhatIf(on)
    setWhatIfPatch({})
  }

  // уходим со страницы — не теряем последнее изменение
  useEffect(
    () => () => {
      window.clearTimeout(timer.current)
      const body = pending.current
      if (!isEmptyPatch(body)) void api.patchSettings(body).catch(() => undefined)
    },
    [],
  )

  const titles = useMemo(() => new Map((current?.weights ?? []).map((w) => [w.code, w.title])), [current?.weights])
  const groups = useMemo(() => groupWeights(current?.weights ?? []), [current?.weights])
  const enabledCount = current?.weights.filter((w) => w.enabled).length ?? 0

  return (
    <>
      <PageHeader
        title="Модель риска"
        description="Коэффициенты применяются сразу, без кнопки «Сохранить»: оценки клиентов пересчитываются за секунды. Хотите сначала посмотреть, что изменится, — включите «Что если»."
      />

      {settings.isPending ? (
        <ModelSkeleton />
      ) : settings.isError || !current ? (
        <Card>
          <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />
        </Card>
      ) : (
        <>
          {/* в режиме «Что если» панель высокая: на телефоне не прилипает, чтобы не закрывать ползунки */}
          <div
            className={cn(
              whatIf ? 'relative md:sticky' : 'sticky',
              'top-14 z-20 -mx-4 mb-4 border-b border-border bg-background/90 px-4 py-2.5 backdrop-blur-md md:-mx-6 md:px-6',
            )}
          >
            {whatIf ? (
              <WhatIfBar
                preview={preview}
                applying={apply.isPending}
                onApply={() => apply.mutate(whatIfPatch)}
                onCancel={() => toggleWhatIf(false)}
              />
            ) : (
              <ImpactBar />
            )}
            <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <StatusLine
                save={save}
                settings={current}
                awaitingVersion={awaitingVersion}
                onRetry={() => {
                  setSave({ kind: 'idle' })
                  void settings.refetch()
                }}
              />
              <div className="flex shrink-0 items-center gap-3">
                <label className="flex cursor-pointer items-center gap-2 text-[13px] font-medium" title="Двигайте ползунки и смотрите, что изменится, до сохранения">
                  <Switch checked={whatIf} onCheckedChange={toggleWhatIf} disabled={apply.isPending || reset.isPending} />
                  Что если
                </label>
                <ConfirmDialog
                  title="Сбросить настройки модели?"
                  description="Все коэффициенты, пороги и горизонт вернутся к значениям по умолчанию. Оценки клиентов пересчитаются автоматически."
                  confirmLabel="Сбросить"
                  destructive
                  onConfirm={() => {
                    window.clearTimeout(timer.current)
                    pending.current = {}
                    reset.mutate()
                  }}
                  trigger={
                    <Button variant="outline" size="sm" disabled={reset.isPending || whatIf}>
                      {reset.isPending ? <Loader2 className="animate-spin" /> : <RotateCcw />}
                      Сбросить к умолчаниям
                    </Button>
                  }
                />
              </div>
            </div>
          </div>

          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
            <div className="space-y-4">
              {groups.length === 0 ? (
                <Card>
                  <EmptyState title="Признаков нет" description="Сервер не вернул ни одного признака модели." />
                </Card>
              ) : (
                groups.map(([group, weights]) => (
                  <Card key={group}>
                    <CardHeader className="flex-row items-baseline justify-between">
                      <CardTitle>{groupLabel(group)}</CardTitle>
                      <span className="text-xs text-muted-foreground num">
                        {weights.filter((w) => w.enabled).length} из {weights.length} включено
                      </span>
                    </CardHeader>
                    <CardContent className="pt-1 pb-2">
                      <ul className="divide-y divide-border">
                        {weights.map((w) => (
                          <WeightRow
                            key={w.code}
                            w={w}
                            onChange={(p) => change({ weights: [{ code: w.code, ...p }] })}
                          />
                        ))}
                      </ul>
                    </CardContent>
                  </Card>
                ))
              )}
              <p className="px-1 text-xs text-subtle-foreground">
                Включено {enabledCount} из {current.weights.length} {plural(current.weights.length, ['признака', 'признаков', 'признаков'])}. Новые признаки
                появляются здесь сами, как только их добавят в модель.
              </p>
            </div>
            <div className="space-y-4">
              {whatIf && <WhatIfChanges preview={preview.ready ? preview.data : undefined} />}
              <ThresholdsCard s={current} onChange={(t) => change({ thresholds: t })} />
              <HorizonCard s={current} onChange={(d) => change({ horizon_days: d })} />
              <HistoryCard titles={titles} />
            </div>
          </div>
        </>
      )}
    </>
  )
}
