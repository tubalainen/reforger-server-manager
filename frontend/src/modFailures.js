// Wording for the mods that stopped a server's last start (#270), from
// GET /api/instances/:id/mod-failures. Kept free of Vue for testing.

const NEED_STATE = {
  not_installed: "isn't installed",
  failed: 'failed to load too',
  installed: 'is installed, yet the engine could not add it',
}

function needName(n) {
  return n.name || n.modId
}

function joinWords(items) {
  if (items.length <= 1) return items.join('')
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

// "Needs WCS_NATO and WCS_RU, which aren't installed."
// A knock-on: "Fails because WCS_Scopes failed to load: fix that one first."
export function failureReason(mod) {
  const needs = mod?.needs || []
  if (!needs.length) return 'The engine could not add it.'
  if (mod.knock_on) {
    const names = joinWords(needs.map(needName))
    return `Fails because ${names} failed to load: fix ${needs.length === 1 ? 'that one' : 'those'} first.`
  }
  const parts = []
  for (const state of ['not_installed', 'failed', 'installed']) {
    const group = needs.filter((n) => n.state === state)
    if (!group.length) continue
    let tail = NEED_STATE[state]
    if (group.length > 1 && state === 'not_installed') tail = "aren't installed"
    if (group.length > 1 && state === 'installed') tail = 'are installed, yet the engine could not add them'
    parts.push(`${joinWords(group.map(needName))}, which ${tail}`)
  }
  return `Needs ${parts.join('; and ')}.`
}

// The mods to fix first: the knock-ons follow from these.
export function rootCauses(failures) {
  return (failures?.mods || []).filter((m) => !m.knock_on)
}

// One line for the server page's banner.
export function failureHeadline(failures) {
  if (!failures?.failed) return ''
  const mods = failures.mods || []
  if (mods.length) {
    const causes = rootCauses(failures)
    const names = joinWords(causes.map((m) => m.name || m.modId))
    const more = mods.length - causes.length
    return (
      `The last start failed: ${names} could not be loaded` +
      (more ? `, and ${more} mod${more === 1 ? '' : 's'} that need${more === 1 ? 's' : ''} ${causes.length === 1 ? 'it' : 'them'}.` : '.')
    )
  }
  if (failures.script_errors?.length) {
    return "The last start failed: a mod's scripts did not compile. The log does not say which mod."
  }
  return 'The last start failed before the server came online.'
}

// The template editor, at the Mods step, with these mods marked.
export function editTemplateRoute(templateId, mods) {
  const ids = (mods || []).map((m) => m.modId).filter(Boolean)
  return {
    name: 'template-edit',
    params: { id: templateId },
    query: { step: 'mods', ...(ids.length ? { failing: ids.join(',') } : {}) },
  }
}

// ?failing=A,B from the template editor's URL, as upper-case ids.
export function failingFromQuery(value) {
  const raw = Array.isArray(value) ? value.join(',') : value || ''
  return raw
    .split(',')
    .map((s) => s.trim().toUpperCase())
    .filter((s) => /^[0-9A-F]{16}$/.test(s))
}
