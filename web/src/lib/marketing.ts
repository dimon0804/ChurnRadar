import type { Marketing, RetentionScenario } from '@/api/types'
import { countLabel, DASH, formatDate, formatDecimal, formatMoney, formatShare, NBSP, plural } from './format'

/** Деньги без центов: на маркетинговой странице копейки только мешают. */
export const eur = (cents: number | null | undefined) => formatMoney(cents, { cents: false })

/** «1,5 мес.», «больше 36 мес.» — для срока жизни клиента. */
export function formatMonths(months: number | null | undefined, capped = false): string {
  if (typeof months !== 'number' || !Number.isFinite(months)) return DASH
  if (capped) return `больше ${formatDecimal(months, 0)}${NBSP}мес.`
  return `${formatDecimal(months, months < 10 ? 1 : 0)}${NBSP}мес.`
}

/** Срок в днях человеческими словами: «3 дня», «5 мес.», «1,1 года». */
export function formatTenure(days: number | null | undefined): string {
  if (typeof days !== 'number' || !Number.isFinite(days)) return DASH
  if (days < 1) return 'меньше дня'
  if (days < 60) {
    const whole = Math.floor(days)
    return `${whole}${NBSP}${plural(whole, ['день', 'дня', 'дней'])}`
  }
  if (days < 365) return `${formatDecimal(days / 30, 0)}${NBSP}мес.`
  const years = Math.round((days / 365) * 10) / 10
  // дробное число лет читается «1,5 года», целое склоняется: «1 год», «2 года», «5 лет»
  const word = Number.isInteger(years) ? plural(years, ['год', 'года', 'лет']) : 'года'
  return `${formatDecimal(years, 1)}${NBSP}${word}`
}

/** «в 4,2 раза», для LTV/CAC. */
export function formatRatio(ratio: number | null | undefined): string {
  if (typeof ratio !== 'number' || !Number.isFinite(ratio)) return DASH
  return `${formatDecimal(ratio, ratio < 10 ? 1 : 0)}×`
}

export type RatioVerdict = 'none' | 'loss' | 'thin' | 'healthy'

/** Как читать LTV/CAC: больше 3 — здоровая экономика, меньше 1 — клиент не окупает привлечение. */
export function ratioVerdict(ratio: number | null | undefined): RatioVerdict {
  if (typeof ratio !== 'number' || !Number.isFinite(ratio)) return 'none'
  if (ratio < 1) return 'loss'
  if (ratio < 3) return 'thin'
  return 'healthy'
}

export const RATIO_TEXT: Record<RatioVerdict, string> = {
  none: 'Укажите стоимость привлечения клиента',
  loss: 'Клиент приносит меньше, чем стоит его привлечь',
  thin: 'Окупается, но запас маленький: ориентир — от 3×',
  healthy: 'Здоровая экономика: клиент приносит минимум втрое больше, чем стоит',
}

/** Евро из поля ввода («12,5» или «12.5») в евроценты. Пусто — null, мусор — NaN. */
export function parseEuro(input: string): number | null {
  const text = input.trim().replace(/\s/g, '').replace(',', '.')
  if (text === '') return null
  if (!/^\d+(\.\d{0,2})?$/.test(text)) return Number.NaN
  return Math.round(Number(text) * 100)
}

/** Проценты из поля ввода в долю 0..1. Мусор — NaN. */
export function parsePercent(input: string): number {
  const text = input.trim().replace(',', '.').replace('%', '')
  if (!/^\d+(\.\d+)?$/.test(text)) return Number.NaN
  return Number(text) / 100
}

const LEVEL_WORDS: Record<string, string> = { high: 'высокого', medium: 'среднего', low: 'низкого' }

export function scenarioTitle(s: RetentionScenario): string {
  const words = s.levels.map((l) => LEVEL_WORDS[l] ?? l)
  return `клиентов ${words.join(' и ')} риска`
}

/** «Если удержим клиентов высокого риска (3 клиента), за 12 месяцев получим ещё €1 240 выручки». */
export function retentionSentence(s: RetentionScenario): string {
  if (s.customers === 0) return `Сейчас нет ${scenarioTitle(s)}, удерживать некого.`
  if (s.revenue_uplift === null || s.target_monthly_churn === null) {
    return `Не с чем сравнить: нет оценённых клиентов, поэтому не знаем, каким станет отток ${scenarioTitle(s)} после удержания.`
  }
  return `Если удержим ${scenarioTitle(s)} (${countLabel(s.customers, ['клиент', 'клиента', 'клиентов'])}), за ${s.horizon_months}${NBSP}мес. получим ещё ${eur(s.revenue_uplift)} выручки.`
}

/** «Выручка этих клиентов за 12 мес.: €374 сейчас → €1 614, если удержим». */
export function retentionRevenueLine(s: RetentionScenario): string | null {
  if (s.revenue_uplift === null || s.revenue_retained === null || s.customers === 0) return null
  return `Выручка этих клиентов за ${s.horizon_months}${NBSP}мес.: ${eur(s.revenue_now)} при нынешнем оттоке → ${eur(s.revenue_retained)}, если удержим.`
}

export function targetSentence(s: RetentionScenario): string {
  if (s.target_monthly_churn === null) return ''
  const source = s.target_source === 'low_risk' ? 'как у клиентов низкого риска' : 'как в среднем по базе (клиентов низкого риска сейчас нет)'
  return (
    `«Удержать» здесь значит снизить их отток до ${formatShare(s.target_monthly_churn)} в месяц, ${source}. ` +
    `Тогда в первый же месяц мы перестанем терять ${eur(s.saved_mrr)} регулярной выручки. ` +
    `Считаем только на год вперёд: выручка клиента за ${s.horizon_months}${NBSP}мес. = MRR × (1 − (1 − отток)^${s.horizon_months}) ÷ отток.`
  )
}

/** «CAC задан администратором 19.10.2026» — откуда в панели стоимость привлечения. */
export function cacSourceText(m: Marketing): string | null {
  if (!m.unit_economics.cac) return null
  const at = m.settings.updated_at ? ` ${formatDate(m.settings.updated_at)}` : ''
  return `CAC задан администратором${at}`
}

/** Пояснения к карточкам: формула и смысл на примере, с числами базы. */
export function kpiHints(m: Marketing) {
  const churn = m.churn.customer_monthly
  const revenueChurn = m.churn.revenue_monthly
  const lifetime = m.lifetime.expected_months
  const margin = m.unit_economics.margin
  const marginText = margin >= 1 ? 'Маржа не задана, поэтому это выручка, а не прибыль.' : `Учтена маржа ${formatShare(margin, 0)}.`
  return {
    mrr:
      `Monthly Recurring Revenue — сколько денег в месяц приносят все действующие услуги. Цены приводим к месяцу: годовая услуга за €120 даёт €10 в месяц, недельная за €7 — €30. ` +
      `Почасовые услуги считаем по тому, сколько клиент реально может заплатить, иначе один сервер за €1 в час давал бы €720. Сейчас ${eur(m.mrr)} с ${countLabel(m.active_customers, ['клиента', 'клиентов', 'клиентов'])}.`,
    arpu:
      `Average Revenue Per User — средний доход с одного клиента в месяц: MRR ÷ активные клиенты = ${eur(m.mrr)} ÷ ${m.active_customers} = ${eur(m.arpu)}. ` +
      `ARPPU — то же, но только по платящим (${m.paying_customers}): ${eur(m.arppu)}. Если все активные платят, они совпадают.`,
    ltv:
      `Lifetime Value — сколько денег клиент принесёт за всё время с нами. Формула: ARPU × маржа ÷ отток в месяц. ` +
      (churn !== null && lifetime !== null
        ? `Модель ждёт, что за месяц уйдёт ${formatShare(churn, 0)} клиентов, значит средний клиент проживёт ещё ${formatMonths(lifetime, m.lifetime.capped)} и принесёт ${eur(m.arpu)} × ${formatDecimal(lifetime, 1)} ≈ ${eur(m.ltv)}. `
        : 'Оценённых клиентов пока нет. ') +
      marginText,
    customerChurn:
      `Какая доля платящих клиентов уйдёт за месяц, по прогнозу модели. Модель оценивает каждого клиента: 70% значит «из десяти таких уйдут семеро». ` +
      `Среднее по базе — ${formatShare(churn)}. Retention (удержание) — обратное число: ${formatShare(m.retention.customer_monthly)} останутся.` +
      (m.churn.fact.monthly_churn !== null && m.churn.fact.customers_left > 0
        ? ` По истории уходило ${formatShare(m.churn.fact.monthly_churn)} в месяц.`
        : ''),
    revenueChurn:
      `Gross Revenue Churn — какую долю регулярной выручки потеряем за месяц из-за ухода клиентов. Отличается от оттока клиентов тем, что крупный клиент весит больше мелкого. ` +
      `Сейчас ${formatShare(revenueChurn)}: это ${eur(m.churn.expected_mrr_loss)} из ${eur(m.mrr)} MRR.`,
    ltvCac:
      `Во сколько раз клиент приносит больше, чем стоит его привлечь: LTV ÷ CAC. Меньше 1 — привлечение в минус, от 3 — здоровая экономика. ` +
      (m.unit_economics.cac
        ? `Payback (окупаемость) — за сколько месяцев клиент вернёт стоимость привлечения: CAC ÷ (ARPU × маржа) = ${eur(m.unit_economics.cac)} ÷ ${eur(m.arpu * margin)}.`
        : 'Стоимости привлечения в данных нет, её задаёт администратор в блоке «Экономика привлечения» ниже.'),
    cacSource: cacSourceText(m),
  }
}

export interface GlossaryItem {
  term: string
  name: string
  text: string
}

export const GLOSSARY: GlossaryItem[] = [
  {
    term: 'MRR',
    name: 'Месячная регулярная выручка',
    text: 'Сколько денег в месяц приносят все действующие услуги. Годовая услуга за €120 — это €10 в месяц.',
  },
  {
    term: 'ARPU',
    name: 'Средний доход с клиента',
    text: 'MRR, делённый на число активных клиентов. Показывает, сколько в среднем платит один клиент за месяц.',
  },
  {
    term: 'ARPPU',
    name: 'Средний доход с платящего клиента',
    text: 'То же, что ARPU, но делим только на тех, кто платит. Полезно, когда много бесплатных пользователей.',
  },
  {
    term: 'LTV',
    name: 'Ценность клиента за всё время',
    text: 'Сколько денег клиент принесёт, пока не уйдёт: доход в месяц × маржа ÷ отток в месяц. Клиент за €10 в месяц, который уходит с вероятностью 10%, принесёт около €100.',
  },
  {
    term: 'CAC',
    name: 'Стоимость привлечения клиента',
    text: 'Сколько стоит привести одного нового клиента: реклама, скидки, работа продаж. Считается вне панели, сюда вводится руками.',
  },
  {
    term: 'Churn',
    name: 'Отток',
    text: 'Доля клиентов, которые ушли за период. Отток 5% в месяц значит: из ста клиентов за месяц уходят пятеро.',
  },
  {
    term: 'Retention',
    name: 'Удержание',
    text: 'Обратное оттоку: какая доля клиентов осталась. При оттоке 5% удержание 95%.',
  },
  {
    term: 'Gross Revenue Churn',
    name: 'Отток выручки',
    text: 'Какую долю регулярной выручки потеряли из-за ушедших клиентов. Уход одного крупного клиента может стоить больше, чем уход десяти мелких.',
  },
  {
    term: 'Payback',
    name: 'Срок окупаемости',
    text: 'За сколько месяцев клиент вернёт деньги, потраченные на его привлечение: CAC ÷ (доход в месяц × маржа).',
  },
  {
    term: 'LTV/CAC',
    name: 'Отдача от привлечения',
    text: 'Во сколько раз клиент приносит больше, чем стоил. Ориентир — от 3: тогда привлечение окупается с запасом.',
  },
]
