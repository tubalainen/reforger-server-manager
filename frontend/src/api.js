// FastAPI reports a 422 as `detail: [{loc, msg, type}, ...]`, not a string. Passing
// that array to Error() stringifies it to "[object Object]" — which is what the
// wizard's config preview used to show for an unnamed template (#85).
export function errorMessage(detail, status) {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((d) => {
        const field = Array.isArray(d?.loc) ? d.loc[d.loc.length - 1] : null
        const msg = d?.msg || 'invalid value'
        return field ? `${field}: ${msg}` : msg
      })
      .join('; ')
  }
  return `HTTP ${status}`
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(errorMessage(detail, status))
    this.status = status
    this.detail = detail
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
