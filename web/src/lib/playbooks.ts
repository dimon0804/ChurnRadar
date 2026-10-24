import type {
  PlaybookAction,
  PlaybookConditions,
  PlaybookInput,
  PlaybookPreset,
  PlaybookUpliftMetric,
  PlaybookUpliftWindow,
} from '@/api/playbooks'
import type { RiskLevel } from '@/api/types'
import { TEMPLATE_TITLE } from '@/lib/contact-today'
import { formatMoney, NBSP } from '@/lib/format'
import { RISK_LABEL } from '@/lib/labels'

/** Состояние формы конструктора: числа — строками, как их вводит человек, деньги — в евро. */
export interface PlaybookDraft {
  name: string
  description: string
  levels: RiskLevel[]
  becameHigh: boolean
  reason: string
  minLoss: string
  noEmailDays: string
  minMrr: string
  maxMrr: string
  autoprolongOff: boolean
  expiresWithin: string
  email: boolean
  template: string
  status: boolean
  statusKind: 'snoozed' | 'resolved'
  statusDays: string
  notify: boolean
  notifyUser: string
  controlShare: number
  maxPerDay: string
  cooldownDays: string
  mode: 'simulation' | 'live'
  enabled: boolean
  preset: string | null
}

export const CONTROL_SHARES = [0, 0.1, 0.2, 0.3, 0.5] as const
export const DEFAULT_CONTROL_SHARE = 0.2

export function emptyDraft(username = 'admin'): PlaybookDraft {
  return {
    name: '',
    description: '',
    levels: [],
    becameHigh: false,
    reason: '',
    minLoss: '',
    noEmailDays: '',
    minMrr: '',
    maxMrr: '',
    autoprolongOff: false,
    expiresWithin: '',
    email: true,
    template: '',
    status: false,
    statusKind: 'snoozed',
    statusDays: '7',
    notify: false,
    notifyUser: username,
    controlShare: DEFAULT_CONTROL_SHARE,
    maxPerDay: '50',
    cooldownDays: '14',
    // новый сценарий всегда сначала в симуляции: сперва смотрим, кого заденет
    mode: 'simulation',
    enabled: true,
    preset: null,
  }
}

const centsToEuros = (cents: number | null | undefined) => (cents == null ? '' : String(cents / 100).replace('.', ','))
const num = (value: number | null | undefined) => (value == null ? '' : String(value))

/** Тело сценария; у готового шаблона полей с серверными умолчаниями может не быть. */
export type PlaybookLike = Pick<PlaybookInput, 'name' | 'conditions' | 'actions'> & Partial<PlaybookInput>

export function toDraft(input: PlaybookLike, username = 'admin'): PlaybookDraft {
  const c = input.conditions
  const base = emptyDraft(username)
  const email = input.actions.find((a) => a.type === 'email')
  const status = input.actions.find((a) => a.type === 'status')
  const notify = input.actions.find((a) => a.type === 'notify')
  return {
    ...base,
    name: input.name,
    description: input.description ?? '',
    levels: (c.risk_levels ?? []) as RiskLevel[],
    becameHigh: Boolean(c.became_high),
    reason: c.main_reason ?? '',
    minLoss: centsToEuros(c.min_expected_loss),
    noEmailDays: num(c.no_email_days),
    minMrr: centsToEuros(c.min_mrr),
    maxMrr: centsToEuros(c.max_mrr),
    autoprolongOff: Boolean(c.autoprolong_off),
    expiresWithin: num(c.expires_within_days),
    email: Boolean(email),
    template: email?.template_code ?? '',
    status: Boolean(status),
    statusKind: status?.status ?? 'snoozed',
    statusDays: status?.days != null ? String(status.days) : base.statusDays,
    notify: Boolean(notify),
    notifyUser: notify?.username ?? username,
    controlShare: input.control_share ?? DEFAULT_CONTROL_SHARE,
    maxPerDay: num(input.max_per_day ?? 50),
    cooldownDays: num(input.cooldown_days ?? 14),
    mode: input.mode ?? 'simulation',
    enabled: input.enabled ?? true,
    preset: input.preset ?? null,
  }
}

export function presetDraft(preset: PlaybookPreset, username = 'admin'): PlaybookDraft {
  const actions = preset.actions.map((a) => (a.type === 'notify' ? { ...a, username } : a))
  return toDraft(
    { name: preset.name, description: preset.description, conditions: preset.conditions, actions, preset: preset.code },
    username,
  )
}

/** «50», «50,5», «1 000» → евроценты. Пусто — null, мусор — NaN. */
export function parseEuros(text: string): number | null {
  const clean = text.replace(/[\s€]/g, '').replace(',', '.')
  if (!clean) return null
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) return Number.NaN
  return Math.round(Number(clean) * 100)
}

export function parseCount(text: string): number | null {
  const clean = text.trim()
  if (!clean) return null
  return /^\d+$/.test(clean) ? Number(clean) : Number.NaN
}

export interface BuildResult {
  input: PlaybookInput | null
  errors: string[]
}

/** Форма → тело запроса. Ошибки — по-человечески, чтобы показать под формой. */
export function buildInput(d: PlaybookDraft): BuildResult {
  const errors: string[] = []
  const field = (label: string, value: number | null, min: number, max: number) => {
    if (value === null) return undefined
    if (Number.isNaN(value) || value < min || value > max) {
      errors.push(`${label}: введите число от ${min} до ${max}`)
      return undefined
    }
    return value
  }
  const money = (label: string, text: string) => {
    const value = parseEuros(text)
    if (value !== null && Number.isNaN(value)) {
      errors.push(`${label}: введите сумму в евро, например 50 или 49,90`)
      return undefined
    }
    return value ?? undefined
  }

  const conditions: PlaybookConditions = {}
  if (d.levels.length) conditions.risk_levels = d.levels
  if (d.becameHigh) conditions.became_high = true
  if (d.reason) conditions.main_reason = d.reason
  const minLoss = money('Ожидаемая потеря', d.minLoss)
  if (minLoss !== undefined) conditions.min_expected_loss = minLoss
  const noEmail = field('Не писали дней', parseCount(d.noEmailDays), 1, 365)
  if (noEmail !== undefined) conditions.no_email_days = noEmail
  const minMrr = money('MRR от', d.minMrr)
  const maxMrr = money('MRR до', d.maxMrr)
  if (minMrr !== undefined) conditions.min_mrr = minMrr
  if (maxMrr !== undefined) conditions.max_mrr = maxMrr
  if (minMrr !== undefined && maxMrr !== undefined && minMrr > maxMrr) errors.push('MRR «от» больше, чем «до»')
  if (d.autoprolongOff) conditions.autoprolong_off = true
  const expires = field('Списание в ближайшие дни', parseCount(d.expiresWithin), 0, 90)
  if (expires !== undefined) conditions.expires_within_days = expires
  if (Object.keys(conditions).length === 0) errors.push('Задайте хотя бы одно условие, иначе сценарий заденет всех клиентов')

  const actions: PlaybookAction[] = []
  if (d.email) actions.push({ type: 'email', template_code: d.template || null })
  if (d.status) {
    if (d.statusKind === 'snoozed') {
      const days = field('Отложить на дней', parseCount(d.statusDays), 1, 365)
      if (days === undefined && !errors.some((e) => e.startsWith('Отложить'))) errors.push('Отложить на дней: укажите срок')
      actions.push({ type: 'status', status: 'snoozed', days: days ?? 1 })
    } else {
      actions.push({ type: 'status', status: 'resolved' })
    }
  }
  if (d.notify) {
    const user = d.notifyUser.trim()
    if (!user) errors.push('Кого уведомить: укажите логин')
    actions.push({ type: 'notify', username: user || 'admin' })
  }
  if (actions.length === 0) errors.push('Выберите хотя бы одно действие')

  const name = d.name.trim()
  if (!name) errors.push('Назовите сценарий')
  const maxPerDay = field('Писем в день', parseCount(d.maxPerDay), 1, 5000)
  const cooldown = field('Не чаще раза в дней', parseCount(d.cooldownDays), 1, 365)
  if (errors.length) return { input: null, errors }
  return {
    errors,
    input: {
      name,
      description: d.description.trim() || null,
      enabled: d.enabled,
      mode: d.mode,
      conditions,
      actions,
      control_share: d.controlShare,
      max_per_day: maxPerDay ?? 50,
      cooldown_days: cooldown ?? 14,
      preset: d.preset,
    },
  }
}

// --- описание словами -----------------------------------------------------------------------

const days = (n: number) => `${n}${NBSP}дн.`

export function describeConditions(c: PlaybookConditions, reasons: Record<string, string> = {}): string[] {
  const out: string[] = []
  if (c.risk_levels?.length) {
    out.push(`риск ${c.risk_levels.map((l) => RISK_LABEL[l as RiskLevel].toLowerCase()).join(' или ')}`)
  }
  if (c.became_high) out.push('риск стал высоким с прошлого пересчёта')
  if (c.main_reason) out.push(`главная причина — «${reasons[c.main_reason] ?? c.main_reason}»`)
  if (c.min_expected_loss != null) out.push(`ожидаемая потеря от ${formatMoney(c.min_expected_loss, { cents: false })}`)
  if (c.no_email_days != null) out.push(`не писали ${days(c.no_email_days)}`)
  if (c.min_mrr != null && c.max_mrr != null) {
    out.push(`MRR от ${formatMoney(c.min_mrr, { cents: false })} до ${formatMoney(c.max_mrr, { cents: false })}`)
  } else if (c.min_mrr != null) {
    out.push(`MRR от ${formatMoney(c.min_mrr, { cents: false })}`)
  } else if (c.max_mrr != null) {
    out.push(`MRR до ${formatMoney(c.max_mrr, { cents: false })}`)
  }
  if (c.autoprolong_off) out.push('выключено автопродление')
  if (c.expires_within_days != null) {
    out.push(c.expires_within_days === 0 ? 'списание сегодня' : `списание в ближайшие ${days(c.expires_within_days)}`)
  }
  return out
}

export function describeAction(a: PlaybookAction): string {
  if (a.type === 'email') {
    return a.template_code ? `письмо «${TEMPLATE_TITLE[a.template_code] ?? a.template_code}»` : 'письмо по главной причине'
  }
  if (a.type === 'status') return a.status === 'resolved' ? 'отметить «Решено»' : `отложить на ${days(a.days ?? 1)}`
  return `уведомить ${a.username ?? ''}`.trim()
}

export function ruleSentence(c: PlaybookConditions, actions: PlaybookAction[], reasons: Record<string, string> = {}): string {
  const when = describeConditions(c, reasons)
  const then = actions.map(describeAction)
  return `Если ${when.join(', ') || 'любой клиент'} → ${then.join(', ')}`
}

// --- эффект ---------------------------------------------------------------------------------

export const WINDOW_LABEL: Record<number, string> = {
  0: 'Всё время',
  7: 'Через 7 дней',
  14: 'Через 14 дней',
  30: 'Через 30 дней',
}

export const VERDICT: Record<PlaybookUpliftMetric['verdict'], { label: string; tone: 'good' | 'bad' | 'neutral' }> = {
  better: { label: 'есть эффект', tone: 'good' },
  worse: { label: 'хуже контроля', tone: 'bad' },
  no_effect: { label: 'не отличить от случайности', tone: 'neutral' },
  few_data: { label: 'мало данных', tone: 'neutral' },
}

/** Разница долей в процентных пунктах: «+12,3 п.п.». */
export function formatPp(diff: number | null | undefined): string {
  if (diff == null || !Number.isFinite(diff)) return '—'
  const value = Math.round(diff * 1000) / 10
  const sign = value > 0 ? '+' : value < 0 ? '−' : ''
  return `${sign}${Math.abs(value).toFixed(1).replace('.', ',')}${NBSP}п.п.`
}

export function formatInterval(m: PlaybookUpliftMetric): string {
  if (m.ci_low == null || m.ci_high == null) return '—'
  return `${formatPp(m.ci_low)} … ${formatPp(m.ci_high)}`
}

export interface UpliftRow {
  metric: string
  treated: number | null
  control: number | null
}

export function upliftRows(w: PlaybookUpliftWindow): UpliftRow[] {
  const pct = (share: number | null | undefined) => (share == null ? null : Math.round(share * 1000) / 10)
  return w.metrics.map((m) => ({ metric: m.title, treated: pct(m.treated_share), control: pct(m.control_share) }))
}

/** Окно, которое показать первым: самое длинное, где уже хватает данных, иначе «всё время». */
export function defaultWindow(windows: PlaybookUpliftWindow[]): number {
  const enough = windows.filter((w) => w.enough).map((w) => w.days)
  return enough.length ? Math.max(...enough) : 0
}

export function shareLabel(share: number): string {
  return share === 0 ? 'без контроля' : `${Math.round(share * 100)}%`
}
