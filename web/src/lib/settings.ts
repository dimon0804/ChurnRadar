import type { FeatureWeight, ModelSettings, ModelSettingsPatch } from '@/api/types'

/** Склеивает два частичных PATCH: веса — по code, пороги — по ключам, остальное — последнее значение. */
export function mergePatch(a: ModelSettingsPatch, b: ModelSettingsPatch): ModelSettingsPatch {
  const out: ModelSettingsPatch = { ...a }
  if (b.horizon_days !== undefined) out.horizon_days = b.horizon_days
  if (b.thresholds) out.thresholds = { ...(a.thresholds ?? {}), ...b.thresholds }
  if (b.weights) {
    const byCode = new Map((a.weights ?? []).map((w) => [w.code, { ...w }]))
    for (const w of b.weights) byCode.set(w.code, { ...(byCode.get(w.code) ?? { code: w.code }), ...w })
    out.weights = [...byCode.values()]
  }
  return out
}

export function isEmptyPatch(p: ModelSettingsPatch): boolean {
  return p.horizon_days === undefined && !p.thresholds && !(p.weights && p.weights.length)
}

/** Применяет PATCH к локальной копии настроек (оптимистичное обновление). */
export function applyPatch(s: ModelSettings, p: ModelSettingsPatch): ModelSettings {
  const weights = p.weights
    ? s.weights.map((w) => {
        const change = p.weights?.find((x) => x.code === w.code)
        return change ? { ...w, ...(change.weight !== undefined && { weight: change.weight }), ...(change.enabled !== undefined && { enabled: change.enabled }) } : w
      })
    : s.weights
  return {
    ...s,
    horizon_days: p.horizon_days ?? s.horizon_days,
    thresholds: { ...s.thresholds, ...(p.thresholds ?? {}) },
    weights,
  }
}

export function groupWeights(weights: FeatureWeight[]): [string, FeatureWeight[]][] {
  const groups = new Map<string, FeatureWeight[]>()
  for (const w of weights) {
    const key = w.group || 'other'
    const list = groups.get(key) ?? []
    list.push(w)
    groups.set(key, list)
  }
  return [...groups.entries()]
}

export function weightStep(w: Pick<FeatureWeight, 'min' | 'max'>): number {
  const span = w.max - w.min
  if (!Number.isFinite(span) || span <= 0) return 1
  return span <= 10 ? 0.1 : 1
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}
