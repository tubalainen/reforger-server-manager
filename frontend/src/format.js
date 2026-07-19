// Shared display formatters.
//
// These lived four times over, inlined in three components, with different bases
// and different "nothing to show" values — which is how a 3.4 GB mod folder came
// to be rendered as "3242.5 MB" (#79): one copy had no GB tier. One tiered
// implementation, with the two things callers legitimately differ on as options.

/**
 * @param {number} n bytes
 * @param {object} [opts]
 * @param {number} [opts.base] 1024 for what is on disk / in memory (the OS's own
 *   units), 1000 for Workshop download sizes (which is how the Workshop lists them).
 * @param {string} [opts.empty] what to show for 0 / null / undefined.
 */
export function formatBytes(n, { base = 1024, empty = '0 B' } = {}) {
  if (!n) return empty
  const gb = base ** 3
  const mb = base ** 2
  if (n >= gb) return (n / gb).toFixed(2) + ' GB'
  if (n >= mb) return (n / mb).toFixed(1) + ' MB'
  return (n / base).toFixed(0) + ' KB'
}

/** Coarse "how long has this been up" — days/hours, hours/minutes, or minutes. */
export function formatUptime(seconds) {
  if (seconds == null) return '—'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m`
}

/** A unix timestamp (seconds) in the viewer's locale; '' when absent. */
export function formatTimestamp(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : ''
}
