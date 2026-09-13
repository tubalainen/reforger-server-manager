// A server's own page (#189): its tabs, its slice of the sparkline history, and the
// filter on the server list beside it. Kept free of Vue so it can be tested alone.

export const SERVER_TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'console', label: 'Console' },
  { key: 'players', label: 'Players' },
  { key: 'saves', label: 'Saves' },
  { key: 'settings', label: 'Settings' },
]

const TAB_KEYS = new Set(SERVER_TABS.map((t) => t.key))

// The tab in the URL, or Overview for none or one that does not exist (an old
// bookmark, a typo) — a server page never renders empty.
export function normalizeTab(tab) {
  return TAB_KEYS.has(tab) ? tab : 'overview'
}

// Route params for a tab: Overview is the bare /servers/:id, the rest add a segment.
export function tabParams(id, tab) {
  return tab === 'overview' ? { id } : { id, tab }
}

// One server's readings of `key` across the history points, oldest first. A point
// taken while the server was not running has no entry for it, which comes back as
// null so the sparkline leaves a gap rather than drawing a zero.
export function serverSeries(history, id, key) {
  const sid = String(id)
  return (history || []).map((p) => {
    const v = p.servers?.[sid]?.[key]
    return typeof v === 'number' ? v : null
  })
}

// The server list's filter: name, scenario or template, case-insensitive.
export function filterServers(servers, query) {
  const q = (query || '').trim().toLowerCase()
  if (!q) return servers
  return servers.filter((s) =>
    [s.name, s.scenario_name, s.template_name].some((f) => (f || '').toLowerCase().includes(q)),
  )
}
