// A server's Settings as one form with one Save (#189). The backend keeps its five
// separate endpoints; this module decides what changed, which endpoints that needs
// and in what order, and how a fresh copy of the server folds into an open form.
// Kept free of Vue so all of it can be tested on its own.

// Fields that rebuild the container, so the backend refuses them while it runs.
export const LOCKED_WHILE_RUNNING = [
  'branch', 'template_id', 'template_version', 'game_port', 'a2s_port', 'rcon_port',
]

const FIELDS = [
  'name', 'branch', 'template_id', 'template_version',
  'game_port', 'a2s_port', 'rcon_port',
  'auto_restart', 'auto_start', 'restart_times', 'mod_auto_update',
]

const TIME_RE = /^([01]?\d|2[0-3]):([0-5]\d)$/

// Daily restart times the way the backend stores them: "HH:MM", sorted, no repeats.
// Anything malformed is dropped here; validate() is what reports it.
export function normaliseTimes(times) {
  const seen = new Set()
  for (const t of times || []) {
    const m = TIME_RE.exec(String(t).trim())
    if (m) seen.add(`${m[1].padStart(2, '0')}:${m[2]}`)
  }
  return [...seen].sort()
}

// The form's values for a server view from /api/instances/:id.
export function settingsFrom(inst) {
  return {
    name: inst.name,
    branch: inst.branch,
    template_id: inst.template_id,
    // the Prod version it is pinned to (#223); null = latest
    template_version: inst.template_version ?? null,
    game_port: inst.game_port,
    a2s_port: inst.a2s_port,
    rcon_port: inst.rcon_port,
    auto_restart: !!inst.auto_restart,
    auto_start: !!inst.auto_start,
    restart_times: normaliseTimes(inst.restart_times),
    // update the template's mods on the scheduled restart (#246)
    mod_auto_update: !!inst.mod_auto_update,
  }
}

function same(a, b) {
  return Array.isArray(a) || Array.isArray(b) ? JSON.stringify(a) === JSON.stringify(b) : a === b
}

export function changedFields(baseline, draft) {
  return FIELDS.filter((f) => !same(baseline[f], draft[f]))
}

/**
 * Fold a fresh copy of the server (a poll, or the answer to a save) into an open
 * form. A field the user has not touched follows the server; a field they have
 * edited keeps their value — and stops counting as a change once the server holds
 * that same value.
 */
export function mergeIncoming(baseline, draft, inst) {
  const incoming = settingsFrom(inst)
  const next = {}
  for (const f of FIELDS) next[f] = same(draft[f], baseline[f]) ? incoming[f] : draft[f]
  return { baseline: incoming, draft: next }
}

// Picking another template starts it at "latest": a pin belongs to one template.
export function pickTemplate(draft, id) {
  return { ...draft, template_id: id, template_version: null }
}

const PORT_FIELDS = ['game_port', 'a2s_port', 'rcon_port']

// What stops a Save, per field.
export function validate(draft) {
  const errors = {}
  if (!String(draft.name || '').trim()) errors.name = 'A server needs a name.'
  else if (String(draft.name).trim().length > 100) errors.name = 'Keep the name to 100 characters.'
  for (const f of PORT_FIELDS) {
    const v = draft[f]
    if (!Number.isInteger(v) || v < 1 || v > 65535) errors[f] = 'A port is a whole number from 1 to 65535.'
  }
  const ports = PORT_FIELDS.map((f) => draft[f])
  if (!errors.game_port && !errors.a2s_port && !errors.rcon_port && new Set(ports).size < 3) {
    errors.game_port = 'Game, A2S and RCON each need their own port.'
  }
  return errors
}

// How each step is named in "Saved the name and the ports".
const STEP_LABEL = {
  ports: 'ports',
  template: 'template',
  restart: 'restart behaviour',
  schedule: 'daily restarts',
  mods: 'mod updates',
}

/**
 * The requests a Save makes, in order. Renaming goes first because it is allowed
 * even while running; the container-rebuilding changes follow; the template goes
 * after the ports so a refused port leaves the world where it was. Each step names
 * the fields it settles.
 */
export function planSave(baseline, draft) {
  const changed = new Set(changedFields(baseline, draft))
  const touched = (fields) => fields.some((f) => changed.has(f))
  const steps = []
  if (touched(['name', 'branch'])) {
    // One endpoint takes both, but say only what actually changed.
    const label = [changed.has('name') && 'name', changed.has('branch') && 'game version']
      .filter(Boolean)
      .join(' and ')
    steps.push({
      key: 'basics',
      label,
      path: '',
      body: { name: String(draft.name).trim(), branch: draft.branch },
    })
  }
  if (touched(PORT_FIELDS)) {
    steps.push({
      key: 'ports',
      label: STEP_LABEL.ports,
      path: '/ports',
      body: { game_port: draft.game_port, a2s_port: draft.a2s_port, rcon_port: draft.rcon_port },
    })
  }
  if (touched(['template_id', 'template_version'])) {
    steps.push({
      key: 'template',
      label: changed.has('template_id') ? STEP_LABEL.template : 'template version',
      path: '/template',
      body: { template_id: draft.template_id, template_version: draft.template_version ?? null },
    })
  }
  if (touched(['auto_restart', 'auto_start'])) {
    steps.push({
      key: 'restart',
      label: STEP_LABEL.restart,
      path: '/restart-settings',
      body: { auto_restart: draft.auto_restart, auto_start: draft.auto_start },
    })
  }
  if (touched(['restart_times'])) {
    steps.push({
      key: 'schedule',
      label: STEP_LABEL.schedule,
      path: '/schedule',
      body: { times: draft.restart_times },
    })
  }
  if (touched(['mod_auto_update'])) {
    steps.push({
      key: 'mods',
      label: STEP_LABEL.mods,
      path: '/mod-updates',
      body: { enabled: draft.mod_auto_update },
    })
  }
  return steps
}

// "the name", "the name and the ports", "the name, the ports and the template"
export function describeSteps(steps) {
  const parts = steps.map((s) => `the ${s.label}`)
  if (parts.length <= 1) return parts.join('')
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}
