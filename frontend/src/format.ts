/** Display helpers. Times show in Lagos time, the institutions' working zone. */

export const DISPLAY_ZONE = 'Africa/Lagos'

const dateTime = new Intl.DateTimeFormat('en-GB', {
  timeZone: DISPLAY_ZONE,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

const dateOnly = new Intl.DateTimeFormat('en-GB', {
  timeZone: DISPLAY_ZONE,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? dateTime.format(new Date(iso)) : '—'
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  // A bare date (2026-11-04) is a calendar day, not a moment: don't shift it by zone.
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split('-').map(Number) as [number, number, number]
    return dateOnly.format(new Date(Date.UTC(y, m - 1, d, 12)))
  }
  return dateOnly.format(new Date(iso))
}

/** Amounts are integers in minor units (kobo for NGN). */
export function formatMoney(minor: number | null | undefined, currency: string | null = 'NGN'): string {
  if (minor === null || minor === undefined) return '—'
  const code = currency || 'NGN'
  try {
    return new Intl.NumberFormat('en-NG', {
      style: 'currency',
      currency: code,
      minimumFractionDigits: 2,
    }).format(minor / 100)
  } catch {
    return `${code} ${(minor / 100).toFixed(2)}`
  }
}

/** How long ago, for queue rows: "3 h", "2 d". */
export function age(iso: string, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 60000))
  if (minutes < 60) return `${minutes} min`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} h`
  return `${Math.round(hours / 24)} d`
}

/** snake_case codes from the API as sentence-case words: in_review -> In review. */
export function humanize(code: string | null | undefined): string {
  if (!code) return '—'
  const words = code.replace(/[._]/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/**
 * Trigger values and rule parameters that hold money in minor units. Mirrors MONEY in the
 * backend's rules/explain.py; parameters say so in their name (min_value_minor).
 */
const MONEY_KEYS = new Set([
  'amount',
  'threshold',
  'total',
  'low',
  'high',
  'credit',
  'debit',
  'average_amount',
])

export function isMoneyKey(key: string): boolean {
  return MONEY_KEYS.has(key) || key.endsWith('_minor')
}

/** A label for a trigger value or parameter key: min_value_minor -> Min value. */
export function fieldLabel(key: string): string {
  return humanize(key.replace(/_minor$/, ''))
}

/** A trigger value or rule parameter, shown as money when the key says it is money. */
export function formatField(key: string, value: unknown, currency: string | null = 'NGN'): string {
  if (typeof value === 'number' && isMoneyKey(key)) return formatMoney(value, currency)
  if (typeof value === 'number' && key.endsWith('percent')) return `${value}%`
  return formatValue(value)
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return value.toLocaleString('en-NG')
  if (Array.isArray(value)) return value.map(formatValue).join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
