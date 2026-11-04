import { describe, expect, it } from 'vitest'

import { age, fieldLabel, formatDate, formatField, formatMoney, humanize } from '@/format'

describe('format', () => {
  it('shows minor units as naira', () => {
    expect(formatMoney(1_234_567)).toMatch(/12,345\.67/)
  })

  it('shows a dash for missing amounts', () => {
    expect(formatMoney(null)).toBe('—')
  })

  it('does not shift a bare date across zones', () => {
    expect(formatDate('2026-03-01')).toMatch(/1 Mar 2026|Mar 1, 2026/)
  })

  it('gives a short age', () => {
    const now = new Date('2026-11-04T12:00:00Z')
    expect(age('2026-11-04T11:15:00Z', now)).toBe('45 min')
    expect(age('2026-11-04T07:00:00Z', now)).toBe('5 h')
    expect(age('2026-11-01T12:00:00Z', now)).toBe('3 d')
  })

  it('shows money trigger values and _minor parameters in naira', () => {
    expect(formatField('amount', 37_257_600)).toMatch(/372,576\.00/)
    expect(formatField('average_amount', 27_230)).toMatch(/272\.30/)
    expect(formatField('min_value_minor', 500_000_00)).toMatch(/500,000\.00/)
    expect(fieldLabel('min_value_minor')).toBe('Min value')
  })

  it('leaves counts and multiples as plain numbers', () => {
    expect(formatField('count', 1)).toBe('1')
    expect(formatField('multiple', 1368.28)).toBe('1,368.28')
  })

  it('turns codes into words', () => {
    expect(humanize('in_review')).toBe('In review')
  })
})
