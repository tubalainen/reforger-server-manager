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

// "Its script scripts/Game/X.c (line 24) does not compile: Undefined function 'Y'.
// It is probably not updated for this game version."
function scriptReason(mod) {
  const [first, ...rest] = mod.errors || []
  if (!first) return 'One of its scripts does not compile.'
  const more = rest.length ? ` (and ${rest.length} more)` : ''
  const shared = (mod.shared_with || []).map(needName)
  return (
    `Its script ${first.file} (line ${first.line}) does not compile: ${first.message.replace(/\.$/, '')}${more}. ` +
    (shared.length
      ? `${joinWords(shared)} ${shared.length === 1 ? 'ships' : 'ship'} the same file, so either may be the cause.`
      : 'It is probably not updated for this game version.')
  )
}

// A failed start's script errors that name no mod: the file is the base game's,
// or no mod folder could be read.
export function unexplainedCompile(failures) {
  if (!failures?.failed || !failures.compile_errors?.length) return false
  return !(failures.mods || []).some((m) => m.reason === 'script')
}

// "scripts/Game/X.c:24: Undefined function 'Y' — in Mod A", the root error first.
export function compileErrorLines(failures) {
  return (failures?.compile_errors || []).map((e) => {
    const owners = (e.owners || []).map(needName)
    return `${e.file}:${e.line}: ${e.message}` + (owners.length ? ` — in ${joinWords(owners)}` : '')
  })
}

// "Needs WCS_NATO and WCS_RU, which aren't installed."
// A knock-on: "Fails because WCS_Scopes failed to load: fix that one first."
export function failureReason(mod) {
  if (mod?.reason === 'script') return scriptReason(mod)
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
    if (causes.length && causes.every((m) => m.reason === 'script')) {
      const what = causes.length === 1 ? 'has a script that does' : 'have scripts that do'
      return `The last start failed: ${names} ${what} not compile.`
    }
    return (
      `The last start failed: ${names} could not be loaded` +
      (more ? `, and ${more} mod${more === 1 ? '' : 's'} that need${more === 1 ? 's' : ''} ${causes.length === 1 ? 'it' : 'them'}.` : '.')
    )
  }
  if (failures.script_errors?.length) {
    return (
      'The last start failed: a script did not compile, and the mod folders on this ' +
      'server do not show which mod it belongs to.'
    )
  }
  return 'The last start failed before the server came online.'
}

// "Not in your template: Escapists RHS Customize faction brings it in."
export function pulledInByText(mod) {
  const by = mod?.pulled_in_by || []
  if (!by.length) return ''
  const names = joinWords(by.map((p) => p.name || p.modId))
  return `Not in your template: ${names} ${by.length === 1 ? 'brings' : 'bring'} it in.`
}

// The template mods that bring in a failing mod which is not in the template
// itself: what there is to remove.
export function pullingMods(mods) {
  const failing = new Set((mods || []).map((m) => m.modId).filter(Boolean))
  const out = new Map()
  for (const m of mods || []) {
    for (const p of m.pulled_in_by || []) if (!failing.has(p.modId)) out.set(p.modId, p)
  }
  return [...out.values()]
}

// The template editor, at the Mods step, with the failing mods marked, and the
// template mods that bring a failing one in marked as needing it.
export function editTemplateRoute(templateId, mods) {
  const ids = (mods || []).map((m) => m.modId).filter(Boolean)
  const pulls = pullingMods(mods).map((p) => p.modId)
  return {
    name: 'template-edit',
    params: { id: templateId },
    query: {
      step: 'mods',
      ...(ids.length ? { failing: ids.join(',') } : {}),
      ...(pulls.length ? { pulls: pulls.join(',') } : {}),
    },
  }
}

// ?failing=A,B (or ?pulls=) from the template editor's URL, as upper-case ids.
export function failingFromQuery(value) {
  const raw = Array.isArray(value) ? value.join(',') : value || ''
  return raw
    .split(',')
    .map((s) => s.trim().toUpperCase())
    .filter((s) => /^[0-9A-F]{16}$/.test(s))
}
