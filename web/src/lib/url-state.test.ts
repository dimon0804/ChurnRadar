import { describe, expect, it } from 'vitest'
import {
  DEFAULT_CUSTOMERS_STATE,
  loginUrl,
  nextSort,
  parseCustomersState,
  safeNext,
  serializeCustomersState,
  toCustomersQuery,
  toggleLevel,
  type CustomersState,
} from './url-state'

const parse = (qs: string) => parseCustomersState(new URLSearchParams(qs))

describe('parseCustomersState', () => {
  it('returns defaults for an empty query', () => {
    expect(parse('')).toEqual(DEFAULT_CUSTOMERS_STATE)
  })

  it('reads every supported parameter', () => {
    expect(parse('sort=balance&order=asc&level=low,high&search=user-7&page=3&page_size=50')).toEqual({
      sort: 'balance',
      order: 'asc',
      levels: ['high', 'low'],
      search: 'user-7',
      page: 3,
      pageSize: 50,
    })
  })

  it('ignores garbage values', () => {
    expect(parse('sort=password&order=sideways&level=extreme,,high,high&page=-2&page_size=9999')).toEqual({
      ...DEFAULT_CUSTOMERS_STATE,
      levels: ['high'],
    })
    expect(parse('page=abc').page).toBe(1)
    expect(parse('page=2.5').page).toBe(1)
  })

  it('picks a sensible order when only sort is given', () => {
    expect(parse('sort=email').order).toBe('asc')
    expect(parse('sort=balance').order).toBe('desc')
  })
})

describe('serializeCustomersState', () => {
  it('omits defaults', () => {
    expect(serializeCustomersState(DEFAULT_CUSTOMERS_STATE).toString()).toBe('')
  })

  it('round-trips', () => {
    const state: CustomersState = {
      sort: 'email',
      order: 'desc',
      levels: ['medium', 'unknown'],
      search: ' a@b ',
      page: 2,
      pageSize: 100,
    }
    const qs = serializeCustomersState(state).toString()
    expect(qs).toBe('sort=email&order=desc&level=medium%2Cunknown&search=a%40b&page=2&page_size=100')
    expect(parse(qs)).toEqual({ ...state, search: 'a@b' })
  })

  it('keeps order when it differs from default even for the default column', () => {
    const qs = serializeCustomersState({ ...DEFAULT_CUSTOMERS_STATE, order: 'asc' }).toString()
    expect(qs).toBe('sort=risk_score&order=asc')
  })
})

describe('sorting and filters', () => {
  it('toggles direction on the same column and resets page', () => {
    const s2 = nextSort({ ...DEFAULT_CUSTOMERS_STATE, page: 4 }, 'risk_score')
    expect(s2).toMatchObject({ sort: 'risk_score', order: 'asc', page: 1 })
    expect(nextSort(s2, 'risk_score').order).toBe('desc')
  })

  it('uses default direction for a new column', () => {
    expect(nextSort(DEFAULT_CUSTOMERS_STATE, 'id')).toMatchObject({ sort: 'id', order: 'asc' })
    expect(nextSort(DEFAULT_CUSTOMERS_STATE, 'total_replenished')).toMatchObject({ order: 'desc' })
  })

  it('toggles levels in canonical order', () => {
    let s = toggleLevel({ ...DEFAULT_CUSTOMERS_STATE, page: 3 }, 'unknown')
    s = toggleLevel(s, 'high')
    expect(s.levels).toEqual(['high', 'unknown'])
    expect(s.page).toBe(1)
    expect(toggleLevel(s, 'high').levels).toEqual(['unknown'])
  })

  it('builds API query params', () => {
    expect(toCustomersQuery({ ...DEFAULT_CUSTOMERS_STATE, levels: ['high', 'medium'], search: '  ' })).toEqual({
      sort: 'risk_score',
      order: 'desc',
      level: 'high,medium',
      search: undefined,
      page: 1,
      page_size: 25,
    })
  })
})

describe('safeNext', () => {
  it.each([
    ['/customers?sort=id', '/customers?sort=id'],
    ['https://evil.example', '/'],
    ['//evil.example', '/'],
    ['/\\evil.example', '/'],
    ['/login?next=/x', '/'],
    ['javascript:alert(1)', '/'],
    [null, '/'],
  ])('%s -> %s', (input, expected) => {
    expect(safeNext(input)).toBe(expected)
  })

  it('loginUrl encodes next', () => {
    expect(loginUrl('/customers?level=high')).toBe('/login?next=%2Fcustomers%3Flevel%3Dhigh')
    expect(loginUrl('/')).toBe('/login')
  })
})
