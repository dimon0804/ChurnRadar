import { describe, expect, it } from 'vitest'
import {
  actorBadge,
  actorName,
  auditExportUrl,
  DEFAULT_AUDIT_FILTERS,
  formatDetails,
  hasActiveFilters,
  hasDetails,
  objectLabel,
  parseAuditFilters,
  serializeAuditFilters,
  toAuditQuery,
  totalPages,
  type AuditFilters,
} from './audit'

const parse = (qs: string) => parseAuditFilters(new URLSearchParams(qs))

describe('parseAuditFilters', () => {
  it('returns defaults for an empty query', () => {
    expect(parse('')).toEqual(DEFAULT_AUDIT_FILTERS)
  })

  it('reads every supported parameter', () => {
    expect(parse('q=aeza&action=token.create&actor_type=token&success=false&date_from=2026-10-01&date_to=2026-10-24&page=3')).toEqual({
      q: 'aeza',
      action: 'token.create',
      actorType: 'token',
      failedOnly: true,
      dateFrom: '2026-10-01',
      dateTo: '2026-10-24',
      page: 3,
    })
  })

  it('ignores garbage', () => {
    expect(parse('actor_type=robot&success=yes&date_from=2026-13-40&date_to=вчера&page=-1')).toEqual(DEFAULT_AUDIT_FILTERS)
    expect(parse('page=2.5').page).toBe(1)
    expect(parse('date_from=2026-02-30').dateFrom).toBe('')
    expect(parse(`q=${'x'.repeat(300)}`).q).toHaveLength(200)
  })
})

describe('serializeAuditFilters', () => {
  it('writes only non-default values', () => {
    expect(serializeAuditFilters(DEFAULT_AUDIT_FILTERS).toString()).toBe('')
    expect(serializeAuditFilters({ ...DEFAULT_AUDIT_FILTERS, q: '  admin ', failedOnly: true, page: 2 }).toString()).toBe(
      'q=admin&success=false&page=2',
    )
  })

  it('round-trips through the URL', () => {
    const filters: AuditFilters = {
      q: 'crm',
      action: 'user',
      actorType: 'user',
      failedOnly: false,
      dateFrom: '2026-10-01',
      dateTo: '',
      page: 4,
    }
    expect(parseAuditFilters(serializeAuditFilters(filters))).toEqual(filters)
  })
})

describe('toAuditQuery', () => {
  it('maps filters to API parameters', () => {
    expect(
      toAuditQuery({ ...DEFAULT_AUDIT_FILTERS, q: ' x ', actorType: 'system', failedOnly: true, dateFrom: '2026-10-01', page: 2 }),
    ).toEqual({ q: 'x', actor_type: 'system', success: false, date_from: '2026-10-01', page: 2, page_size: 50 })
  })

  it('swaps reversed dates instead of returning an empty log', () => {
    const q = toAuditQuery({ ...DEFAULT_AUDIT_FILTERS, dateFrom: '2026-10-24', dateTo: '2026-10-01' }, 20)
    expect(q).toMatchObject({ date_from: '2026-10-01', date_to: '2026-10-24', page_size: 20 })
  })

  it('does not send success=true: "only failures" off means everything', () => {
    expect(toAuditQuery(DEFAULT_AUDIT_FILTERS)).toEqual({ page: 1, page_size: 50 })
  })
})

describe('auditExportUrl', () => {
  it('uses the same filters but no pagination', () => {
    expect(auditExportUrl(DEFAULT_AUDIT_FILTERS)).toBe('/api/v1/audit/export.csv')
    expect(
      auditExportUrl({ ...DEFAULT_AUDIT_FILTERS, q: 'ip 10.0', action: 'token.create', failedOnly: true, dateTo: '2026-10-24', page: 7 }),
    ).toBe('/api/v1/audit/export.csv?q=ip+10.0&action=token.create&success=false&date_to=2026-10-24')
  })
})

describe('hasActiveFilters', () => {
  it('ignores the page number', () => {
    expect(hasActiveFilters({ ...DEFAULT_AUDIT_FILTERS, page: 5 })).toBe(false)
    expect(hasActiveFilters({ ...DEFAULT_AUDIT_FILTERS, failedOnly: true })).toBe(true)
    expect(hasActiveFilters({ ...DEFAULT_AUDIT_FILTERS, q: '   ' })).toBe(false)
  })
})

describe('actor helpers', () => {
  it('strips the token: prefix', () => {
    expect(actorName({ actor: 'token:aeza-crm', actor_type: 'token' })).toBe('aeza-crm')
    expect(actorName({ actor: 'admin', actor_type: 'user' })).toBe('admin')
    expect(actorName({ actor: '', actor_type: 'anonymous' })).toBe('без входа')
  })

  it('badges everyone except employees', () => {
    expect(actorBadge('user')).toBeNull()
    expect(actorBadge('token')).toBe('токен')
    expect(actorBadge('system')).toBe('система')
    expect(actorBadge('anonymous')).toBe('без входа')
  })
})

describe('objectLabel', () => {
  it('translates known types and keeps unknown ones', () => {
    expect(objectLabel({ object_type: 'token', object_id: 'aeza' })).toBe('токен aeza')
    expect(objectLabel({ object_type: 'widget', object_id: '7' })).toBe('widget 7')
    expect(objectLabel({ object_type: null, object_id: '42' })).toBe('42')
    expect(objectLabel({ object_type: null, object_id: null })).toBe('—')
  })
})

describe('details', () => {
  it('pretty-prints non-empty details only', () => {
    expect(hasDetails({})).toBe(false)
    expect(hasDetails(null)).toBe(false)
    expect(formatDetails({})).toBe('')
    expect(formatDetails({ role: 'analyst' })).toBe('{\n  "role": "analyst"\n}')
  })
})

describe('totalPages', () => {
  it('never returns zero', () => {
    expect(totalPages(0)).toBe(1)
    expect(totalPages(50)).toBe(1)
    expect(totalPages(51)).toBe(2)
    expect(totalPages(101, 25)).toBe(5)
  })
})
