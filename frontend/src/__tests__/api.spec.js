import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, clientId, errorMessage } from '../api'

describe('errorMessage', () => {
  it('reads a FastAPI 422 instead of showing "[object Object]" (#85)', () => {
    // FastAPI reports validation errors as an ARRAY of objects, not a string.
    // Passing that to Error() stringified it, and the wizard's config preview
    // showed "// [object Object]" for an unnamed template.
    const detail = [
      { type: 'string_too_short', loc: ['body', 'name'], msg: 'String should have at least 1 character' },
    ]
    expect(errorMessage(detail, 422)).toBe('name: String should have at least 1 character')
  })

  it('joins several validation errors', () => {
    const detail = [
      { loc: ['body', 'name'], msg: 'Field required' },
      { loc: ['body', 'scenario_id'], msg: 'Field required' },
    ]
    expect(errorMessage(detail, 422)).toBe('name: Field required; scenario_id: Field required')
  })

  it('passes a plain string detail straight through', () => {
    expect(errorMessage('Instance not found', 404)).toBe('Instance not found')
  })

  it('falls back to the status when there is nothing to say', () => {
    expect(errorMessage('', 500)).toBe('HTTP 500')
    expect(errorMessage(undefined, 502)).toBe('HTTP 502')
    expect(errorMessage([], 400)).toBe('HTTP 400')
  })

  it('copes with a malformed detail entry rather than throwing', () => {
    expect(errorMessage([{}], 422)).toBe('invalid value')
  })
})

describe('clientId', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('rides along on every request so the backend can tell tabs apart (#102)', async () => {
    expect(clientId).toBeTruthy()
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    await api('/api/templates')
    await api('/api/templates/1/lock', { method: 'POST' })
    const headers = fetchMock.mock.calls.map(([, init]) => init.headers['X-Client-Id'])
    expect(headers).toEqual([clientId, clientId]) // present and stable
  })
})

describe('ApiError', () => {
  it('carries the status and a readable message', () => {
    const err = new ApiError(409, 'Stop the server before clearing its data')
    expect(err.status).toBe(409)
    expect(err.message).toBe('Stop the server before clearing its data')
    expect(err instanceof Error).toBe(true)
  })
})
