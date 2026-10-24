import { describe, expect, it } from 'vitest'
import type { AlertItem } from '@/api/alerts'
import { badgeText, countUnread } from './alerts'

const item = (id: number): AlertItem => ({
  id,
  kind: 'high_risk',
  customer_id: id,
  email: null,
  risk_level: 'high',
  prev_level: 'medium',
  expected_loss: 100,
  prev_loss: 50,
  message: 'm',
  created_at: null,
  link: `/customers/${id}`,
})

describe('countUnread', () => {
  it('считает только то, что новее просмотренного', () => {
    expect(countUnread([item(7), item(6), item(5)], 5)).toBe(2)
    expect(countUnread([item(7)], 7)).toBe(0)
  })

  it('при первом визите не показывает архив как новое', () => {
    expect(countUnread([item(3), item(2)], null)).toBe(0)
  })
})

describe('badgeText', () => {
  it('сворачивает большие числа', () => {
    expect(badgeText(3)).toBe('3')
    expect(badgeText(9)).toBe('9')
    expect(badgeText(42)).toBe('9+')
  })
})
