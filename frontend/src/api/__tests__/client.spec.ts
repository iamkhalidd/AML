import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError, readCookie, setSignedOutHandler, withQuery } from '@/api/client'

function respond(status: number, body?: unknown) {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(
    async () =>
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  setSignedOutHandler(null)
  document.cookie = 'aml_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
})

describe('readCookie', () => {
  it('finds a cookie among others and decodes it', () => {
    expect(readCookie('aml_csrf', 'a=1; aml_csrf=tok%20en; b=2')).toBe('tok en')
  })

  it('returns null when the cookie is missing', () => {
    expect(readCookie('aml_csrf', 'a=1')).toBeNull()
  })
})

describe('withQuery', () => {
  it('skips empty values and repeats arrays', () => {
    expect(withQuery('/v1/alerts', { status: ['open', 'in_review'], q: '', limit: 50, x: null })).toBe(
      '/v1/alerts?status=open&status=in_review&limit=50',
    )
  })

  it('leaves the path alone with nothing to add', () => {
    expect(withQuery('/v1/alerts', { q: undefined })).toBe('/v1/alerts')
  })
})

describe('request', () => {
  it('sends the CSRF token on writes but not reads', async () => {
    document.cookie = 'aml_csrf=abc123'
    const fetchMock = respond(200, {})
    await api.get('/v1/alerts')
    await api.post('/v1/alerts/1/claim')
    const [, read] = fetchMock.mock.calls[0]!
    const [, write] = fetchMock.mock.calls[1]!
    expect(new Headers(read?.headers).get('X-CSRF-Token')).toBeNull()
    expect(new Headers(write?.headers).get('X-CSRF-Token')).toBe('abc123')
  })

  it('turns a problem body into an ApiError', async () => {
    respond(409, { title: 'Conflict', status: 409, code: 'ALERT_CLOSED', detail: 'Already closed' })
    const error = await api.post('/v1/alerts/1/close', {}).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 409, code: 'ALERT_CLOSED', message: 'Already closed' })
  })

  it('tells the app when the session has ended', async () => {
    respond(401, { title: 'Unauthorized', status: 401, code: 'NOT_SIGNED_IN' })
    const handler = vi.fn<() => void>()
    setSignedOutHandler(handler)
    await api.get('/v1/alerts').catch(() => undefined)
    expect(handler).toHaveBeenCalledOnce()
  })

  it('returns nothing for 204', async () => {
    respond(204)
    await expect(api.post('/v1/auth/sign-out')).resolves.toBeUndefined()
  })
})
