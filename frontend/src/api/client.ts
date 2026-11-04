/**
 * Calls to the AML API under /v1, on the same origin as the app.
 *
 * The session lives in an HttpOnly cookie the browser sends by itself. Requests that change
 * state also send the CSRF token, read from the aml_csrf cookie, in the X-CSRF-Token header.
 * Errors arrive as problem bodies and are thrown as ApiError.
 */

export interface Problem {
  type?: string
  title: string
  status: number
  code: string
  detail?: string
  request_id?: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: string

  constructor(problem: Problem) {
    super(problem.detail || problem.title)
    this.name = 'ApiError'
    this.status = problem.status
    this.code = problem.code
    this.detail = problem.detail || problem.title
  }
}

export const CSRF_COOKIE = 'aml_csrf'
export const CSRF_HEADER = 'X-CSRF-Token'

export function readCookie(name: string, source: string = document.cookie): string | null {
  for (const part of source.split(';')) {
    const [key, ...rest] = part.trim().split('=')
    if (key === name) return decodeURIComponent(rest.join('='))
  }
  return null
}

type Query = Record<string, string | number | boolean | string[] | null | undefined>

export function withQuery(path: string, query?: Query): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item))
    else params.append(key, String(value))
  }
  const text = params.toString()
  return text ? `${path}?${text}` : path
}

/** Called when the server says the session has ended, so the app can go to sign-in. */
let onSignedOut: (() => void) | null = null
export function setSignedOutHandler(handler: (() => void) | null): void {
  onSignedOut = handler
}

async function problemFrom(response: Response): Promise<Problem> {
  try {
    const body = await response.json()
    if (body && typeof body.code === 'string') return body as Problem
    // FastAPI's own validation errors come as {detail: [...]}.
    if (body && Array.isArray(body.detail)) {
      const first = body.detail[0]
      return {
        title: 'Check the form',
        status: response.status,
        code: 'VALIDATION_FAILED',
        detail: first?.msg ?? 'Some values are not valid',
      }
    }
  } catch {
    // Not JSON: fall through to a generic problem.
  }
  return {
    title: response.statusText || 'Request failed',
    status: response.status,
    code: response.status >= 500 ? 'SERVER_ERROR' : 'REQUEST_FAILED',
    detail:
      response.status >= 500
        ? 'The server could not answer. It may be waking up; try again in a minute.'
        : 'The request failed.',
  }
}

export async function request<T>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  options: { query?: Query; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') {
    const csrf = readCookie(CSRF_COOKIE)
    if (csrf) headers[CSRF_HEADER] = csrf
  }
  let response: Response
  try {
    const init: RequestInit = { method, headers, credentials: 'same-origin' }
    if (options.body !== undefined) init.body = JSON.stringify(options.body)
    response = await fetch(withQuery(path, options.query), init)
  } catch {
    throw new ApiError({
      title: 'No connection',
      status: 0,
      code: 'NETWORK',
      detail: 'Could not reach the server. Check your connection and try again.',
    })
  }
  if (!response.ok) {
    const problem = await problemFrom(response)
    if (problem.code === 'NOT_SIGNED_IN' && onSignedOut) onSignedOut()
    throw new ApiError(problem)
  }
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>('GET', path, { query }),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, { body }),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, { body }),
}
