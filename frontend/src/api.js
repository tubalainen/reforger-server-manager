export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`)
    this.status = status
  }
}

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail = ''
    try {
      detail = (await res.json()).detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  return res.status === 204 ? null : res.json()
}
