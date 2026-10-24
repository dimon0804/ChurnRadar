import { describe, expect, it } from 'vitest'
import {
  curlExample,
  describeSchedule,
  expiresSoon,
  expiryDays,
  formatAllowedIps,
  formatExpiry,
  formatLastUsed,
  isIpOrCidr,
  parseEmails,
  parseIpList,
  parseRateLimit,
  TOKEN_ROLE_LABEL,
  TOKEN_STATUS_LABEL,
  tokenNameError,
} from './integrations'

const NOW = new Date('2026-10-24T12:00:00Z')

describe('labels', () => {
  it('names every role and status in Russian', () => {
    expect(TOKEN_ROLE_LABEL).toEqual({ admin: 'Администратор', marketer: 'Маркетолог', analyst: 'Аналитик' })
    expect(TOKEN_STATUS_LABEL.revoked).toBe('Отозван')
    expect(TOKEN_STATUS_LABEL.expired).toBe('Истёк')
  })
})

describe('expiryDays', () => {
  it('maps form choices to expires_in_days', () => {
    expect(expiryDays('30')).toBe(30)
    expect(expiryDays('90')).toBe(90)
    expect(expiryDays('365')).toBe(365)
    expect(expiryDays('never')).toBeNull()
  })
})

describe('tokenNameError', () => {
  it('accepts the server pattern', () => {
    expect(tokenNameError('aeza-billing')).toBeNull()
    expect(tokenNameError('crm_1.prod')).toBeNull()
    expect(tokenNameError('  e2e-123  ')).toBeNull()
  })

  it('rejects empty, cyrillic, leading punctuation and too long names', () => {
    expect(tokenNameError('')).toMatch(/имя/)
    expect(tokenNameError('биллинг')).toMatch(/латиница/)
    expect(tokenNameError('-crm')).toMatch(/латиница/)
    expect(tokenNameError('a b')).toMatch(/латиница/)
    expect(tokenNameError('a'.repeat(65))).toMatch(/64/)
    expect(tokenNameError('a'.repeat(64))).toBeNull()
  })
})

describe('isIpOrCidr', () => {
  it.each(['203.0.113.10', '10.0.0.0/8', '0.0.0.0/0', '192.168.1.1/32', '::1', '2001:db8::/32', 'fe80::1:2'])('accepts %s', (v) => {
    expect(isIpOrCidr(v)).toBe(true)
  })

  it.each(['256.1.1.1', '10.0.0.0/33', '1.2.3', 'example.com', '2001:db8::/129', '1::2::3', '10.0.0.1/8/1', '01.2.3.4', ''])(
    'rejects %s',
    (v) => {
      expect(isIpOrCidr(v)).toBe(false)
    },
  )
})

describe('parseIpList', () => {
  it('splits by lines, commas, semicolons and spaces, drops duplicates', () => {
    expect(parseIpList('203.0.113.10\n10.0.0.0/8, 203.0.113.10;  2001:db8::/32\n\n')).toEqual({
      ips: ['203.0.113.10', '10.0.0.0/8', '2001:db8::/32'],
      invalid: [],
    })
  })

  it('reports garbage separately', () => {
    expect(parseIpList('1.2.3.4, офис, 999.1.1.1, офис')).toEqual({ ips: ['1.2.3.4'], invalid: ['офис', '999.1.1.1'] })
  })

  it('returns an empty list for blank input — token works from any address', () => {
    expect(parseIpList('  \n ')).toEqual({ ips: [], invalid: [] })
  })
})

describe('parseRateLimit', () => {
  it('treats empty as the shared limit', () => {
    expect(parseRateLimit('')).toEqual({ value: null, error: null })
    expect(parseRateLimit('   ')).toEqual({ value: null, error: null })
  })

  it('parses positive integers, including grouped digits', () => {
    expect(parseRateLimit('120')).toEqual({ value: 120, error: null })
    expect(parseRateLimit('10 000')).toEqual({ value: 10000, error: null })
  })

  it('rejects zero, fractions, negatives and huge values', () => {
    expect(parseRateLimit('0').error).toBeTruthy()
    expect(parseRateLimit('1.5').error).toBeTruthy()
    expect(parseRateLimit('-3').error).toBeTruthy()
    expect(parseRateLimit('100001').error).toBeTruthy()
  })
})

describe('formatLastUsed', () => {
  const now = NOW.getTime()

  it('says the token was never used', () => {
    expect(formatLastUsed({ last_used_at: null, last_used_ip: null }, now)).toBe('ещё не использовался')
    expect(formatLastUsed({ last_used_at: 'garbage', last_used_ip: null }, now)).toBe('ещё не использовался')
  })

  it('shows how long ago and from where', () => {
    expect(formatLastUsed({ last_used_at: '2026-10-24T11:55:00Z', last_used_ip: '203.0.113.10' }, now)).toBe(
      '5 мин назад · 203.0.113.10',
    )
    expect(formatLastUsed({ last_used_at: '2026-10-22T12:00:00Z', last_used_ip: null }, now)).toBe('2 дня назад')
  })

  it('does not go negative when clocks drift', () => {
    expect(formatLastUsed({ last_used_at: '2026-10-24T12:00:30Z', last_used_ip: null }, now)).toBe('0 с назад')
  })
})

describe('formatExpiry', () => {
  it('handles every status', () => {
    expect(formatExpiry({ status: 'active', expires_at: null, revoked_at: null }, NOW)).toBe('бессрочный')
    expect(formatExpiry({ status: 'active', expires_at: '2027-01-22T12:00:00Z', revoked_at: null }, NOW)).toBe('до 22 янв 2027')
    expect(formatExpiry({ status: 'expired', expires_at: '2026-10-03T12:00:00Z', revoked_at: null }, NOW)).toBe('истёк 3 окт')
    expect(formatExpiry({ status: 'revoked', expires_at: null, revoked_at: null }, NOW)).toBe('отозван')
    expect(formatExpiry({ status: 'revoked', expires_at: null, revoked_at: '2026-10-20T09:05:00' }, NOW)).toBe(
      'отозван 20 окт, 09:05',
    )
  })

  it('counts days when the end is close', () => {
    expect(formatExpiry({ status: 'active', expires_at: '2026-10-27T12:00:00Z', revoked_at: null }, NOW)).toBe(
      'до 27 окт (ещё 3 дня)',
    )
  })
})

describe('expiresSoon', () => {
  const now = NOW.getTime()
  it('flags only active tokens that end within two weeks', () => {
    expect(expiresSoon({ status: 'active', expires_at: '2026-10-30T00:00:00Z' }, now)).toBe(true)
    expect(expiresSoon({ status: 'active', expires_at: '2027-10-30T00:00:00Z' }, now)).toBe(false)
    expect(expiresSoon({ status: 'active', expires_at: null }, now)).toBe(false)
    expect(expiresSoon({ status: 'revoked', expires_at: '2026-10-30T00:00:00Z' }, now)).toBe(false)
  })
})

describe('formatAllowedIps', () => {
  it('lists addresses or says any', () => {
    expect(formatAllowedIps([])).toBe('с любого адреса')
    expect(formatAllowedIps(['1.2.3.4', '10.0.0.0/8'])).toBe('1.2.3.4, 10.0.0.0/8')
  })
})

describe('curlExample', () => {
  it('builds a ready-to-paste command', () => {
    expect(curlExample('https://churn.example.com/')).toBe(
      'curl -H "Authorization: Bearer <токен>" https://churn.example.com/api/v1/customers',
    )
    expect(curlExample('http://localhost:8080', 'cr_abc')).toBe(
      'curl -H "Authorization: Bearer cr_abc" http://localhost:8080/api/v1/customers',
    )
  })
})

describe('parseEmails', () => {
  it('splits, validates and dedupes case-insensitively', () => {
    expect(parseEmails('boss@example.com, CFO@example.com\nboss@EXAMPLE.com; не-адрес')).toEqual({
      emails: ['boss@example.com', 'CFO@example.com'],
      invalid: ['не-адрес'],
    })
    expect(parseEmails('')).toEqual({ emails: [], invalid: [] })
  })
})

describe('describeSchedule', () => {
  it('describes enabled and disabled schedules', () => {
    expect(describeSchedule({ enabled: true, weekday: 1, send_time: '09:00', timezone: 'Europe/Moscow' })).toBe(
      'Уходит по понедельникам в 09:00 (Europe/Moscow)',
    )
    expect(describeSchedule({ enabled: false, weekday: 3, send_time: '09:00', timezone: 'Europe/Moscow' })).toBe(
      'Рассылка выключена',
    )
  })
})
