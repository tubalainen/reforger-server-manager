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

// Per-tab identity for template edit locks (#102), sent on every request.
// sessionStorage scopes it to the tab — two tabs of the same login are still
// two editors racing each other, which is exactly what the lock protects
// against. crypto.randomUUID needs a secure context, which a plain-HTTP LAN
// deployment (WEB_BIND=0.0.0.0) is not, hence the getRandomValues fallback.
function newClientId() {
  if (crypto.randomUUID) return crypto.randomUUID()
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
    b.toString(16).padStart(2, '0'),
  ).join('')
}

export const clientId = (() => {
  try {
    let id = sessionStorage.getItem('rsm_client_id')
    if (!id) {
      id = newClientId()
      sessionStorage.setItem('rsm_client_id', id)
    }
    return id
  } catch {
    return newClientId() // no sessionStorage (tests); a fresh id per load is fine
  }
})()

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: {
      'X-Client-Id': clientId,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
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
