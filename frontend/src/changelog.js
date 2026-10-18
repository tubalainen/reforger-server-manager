// Change-log grouping (#112, #223). Kept free of Vue so it can be tested.
//
// Entries arrive newest event first, lines of one save sharing a changed_at.
// A save comment or release note is a line of its own ('note'); it is lifted
// out of the group's lines so the modal can show it as the group's comment.

export const CATEGORY = {
  meta: { label: 'template', cls: 'text-bg-secondary' },
  scenario: { label: 'scenario', cls: 'text-bg-success' },
  mod: { label: 'mod', cls: 'text-bg-primary' },
  setting: { label: 'setting', cls: 'text-bg-info' },
  release: { label: 'stage', cls: 'text-bg-warning' },
}

export const categoryBadge = (c) => CATEGORY[c] || { label: c, cls: 'text-bg-secondary' }

export function groupEntries(entries) {
  const out = []
  for (const e of entries) {
    let last = out[out.length - 1]
    if (!last || last.at !== e.changed_at) {
      // `display` is pre-formatted server-side in the manager's timezone (#112).
      last = { at: e.changed_at, display: e.display, key: `${e.changed_at}${e.id}`, notes: [], items: [] }
      out.push(last)
    }
    if (e.category === 'note') last.notes.push(e.summary)
    else last.items.push(e)
  }
  return out
}

// Search matches a line or a comment; a group shows when anything in it does.
export function filterEntries(entries, query) {
  const q = (query || '').trim().toLowerCase()
  if (!q) return entries
  return entries.filter((e) => e.summary.toLowerCase().includes(q))
}
