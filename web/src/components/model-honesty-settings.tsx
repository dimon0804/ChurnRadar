import { AlertTriangle } from 'lucide-react'
import { useId } from 'react'
import type { ModelSettings, SettingsGroup } from '@/api/types'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { formatDecimal } from '@/lib/format'
import { clamp } from '@/lib/settings'
import { cn } from '@/lib/utils'

/** Предупреждение: таблица калибровки обучена на других весах, вероятности приблизительные. */
export function CalibrationMismatch({ s }: { s: ModelSettings }) {
  if (s.calibration?.matches_settings !== false || s.calibrated === false) return null
  return (
    <div
      className="flex items-start gap-3 rounded-xl border border-risk-medium/30 bg-risk-medium-bg px-4 py-3 text-sm leading-6"
      role="status"
      data-testid="calibration-mismatch"
    >
      <AlertTriangle className="mt-1 size-4 shrink-0 text-risk-medium" aria-hidden />
      <p>
        Таблицу калибровки обучали на других весах и группах, не на тех, что стоят сейчас. Деньги и прогноз всё ещё
        считаются по ней, но цифры приблизительные. Чтобы они снова стали точными, пересчитайте калибровку или верните веса,
        на которых её обучали.
      </p>
    </div>
  )
}

export function CalibrationCard({ s, onChange }: { s: ModelSettings; onChange: (calibrated: boolean) => void }) {
  const id = useId()
  if (s.calibrated === undefined) return null
  const available = s.calibration?.available !== false
  return (
    <Card data-testid="settings-calibrated">
      <CardHeader>
        <CardTitle>Вероятность для денег</CardTitle>
        <CardDescription>Score расставляет клиентов по очереди, а сколько денег уйдёт, лучше считать по калиброванной вероятности.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-start gap-3">
          <Switch id={id} checked={s.calibrated && available} disabled={!available} onCheckedChange={onChange} className="mt-0.5" />
          <label htmlFor={id} className={cn('cursor-pointer text-sm font-medium', !available && 'opacity-55')}>
            Считать деньги по калиброванной вероятности
          </label>
        </div>
        <p className="text-[13px] leading-5 text-muted-foreground">
          {available
            ? 'Включено: ожидаемая потеря и прогноз непродлений считаются по вероятности, которую сверили с фактом. Выключено: по сырому score, как раньше. Порядок клиентов и уровни риска от этого не меняются.'
            : 'В этой сборке нет таблицы калибровки, поэтому деньги считаются по сырому score.'}
        </p>
      </CardContent>
    </Card>
  )
}

function GroupRow({
  g,
  titles,
  onChange,
}: {
  g: SettingsGroup
  titles: ReadonlyMap<string, string>
  onChange: (patch: { cap?: number; enabled?: boolean }) => void
}) {
  const titleId = useId()
  const features = g.features.map((code) => titles.get(code) ?? code)
  return (
    <li className="space-y-2.5 py-3" data-group={g.code}>
      <div className="flex items-start gap-3">
        <Switch checked={g.enabled} onCheckedChange={(enabled) => onChange({ enabled })} aria-labelledby={titleId} className="mt-0.5" />
        <div className="min-w-0">
          <p id={titleId} className="text-sm font-medium">
            Группа «{g.title}»
          </p>
          {g.description && <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{g.description}</p>}
          {features.length > 0 && <p className="mt-0.5 text-xs text-subtle-foreground">Признаки: {features.join(', ')}</p>}
        </div>
      </div>
      <div className={cn('grid grid-cols-[minmax(0,1fr)_72px] items-center gap-3 pl-12', !g.enabled && 'opacity-55')}>
        <Slider
          value={[g.cap]}
          min={0}
          max={100}
          step={1}
          disabled={!g.enabled}
          onValueChange={([v]) => v !== undefined && onChange({ cap: v })}
          thumbLabels={[`Потолок группы «${g.title}»`]}
        />
        <Input
          inputMode="numeric"
          aria-label={`Потолок группы «${g.title}», пунктов`}
          className="h-8 text-right num"
          disabled={!g.enabled}
          value={formatDecimal(g.cap, 0)}
          onChange={(e) => {
            const v = Number(e.target.value.replace(',', '.'))
            if (e.target.value.trim() !== '' && Number.isFinite(v)) onChange({ cap: clamp(Math.round(v), 0, 100) })
          }}
        />
      </div>
      {g.enabled && Math.abs(g.cap - g.default_cap) >= 0.5 && (
        <button
          type="button"
          onClick={() => onChange({ cap: g.default_cap })}
          className="ml-12 rounded text-xs text-link outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          Вернуть {formatDecimal(g.default_cap, 0)} по умолчанию
        </button>
      )}
    </li>
  )
}

export function GroupsCard({
  s,
  titles,
  onChange,
}: {
  s: ModelSettings
  titles: ReadonlyMap<string, string>
  onChange: (code: string, patch: { cap?: number; enabled?: boolean }) => void
}) {
  const groups = s.groups ?? []
  if (groups.length === 0) return null
  return (
    <Card data-testid="settings-groups">
      <CardHeader>
        <CardTitle>Группы признаков</CardTitle>
        <CardDescription>
          Некоторые признаки говорят об одной и той же проблеме. Если просто сложить их вклады, одна неоплата посчитается
          дважды или трижды. В группе вклады объединяются, а общий вклад не поднимается выше потолка.
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-0 pb-2">
        <ul className="divide-y divide-border">
          {groups.map((g) => (
            <GroupRow key={g.code} g={g} titles={titles} onChange={(p) => onChange(g.code, p)} />
          ))}
        </ul>
        <p className="pb-2 text-xs leading-5 text-subtle-foreground">
          Потолок — сколько пунктов score группа может дать максимум. По умолчанию группа выключена: на проверке она не
          улучшила качество, подробности на странице «Качество модели».
        </p>
      </CardContent>
    </Card>
  )
}
