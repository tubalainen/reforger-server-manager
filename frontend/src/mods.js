// Pure helpers for the mod dependency graph (issue #55).
//
// A mod entry is { modId, name, version, versions, explicit, from_scenario,
// dependencies } where `version` is a user-locked version (null = follow the
// Workshop's latest release, #60), `versions` is the published version history
// for the lock picker, `explicit` marks a user/scenario-chosen "root",
// `dependencies` are the direct dependency modIds (graph edges), and
// `from_scenario` flags the mod that backs the currently selected scenario.
// These live only in the manager; the server's config.json gets the flat
// modId/name list, plus version only where the user locked one.

// @2 (#164) differs from @1 in one way: the array order IS the load order. In an
// @1 file the order was only ever "picks, then dependencies", so an @1 file is
// re-partitioned on import to land exactly where it left off.
export const MODS_FILE_FORMAT = 'reforger-server-manager/mods@2'
export const MODS_FILE_FORMAT_V1 = 'reforger-server-manager/mods@1'

// Workshop asset ids are 16 hex digits. Pull one out of a bare id, "{id}-slug",
// or a full workshop URL — mirrors the backend's normalize_asset_id. Returns the
// upper-cased id, or null when there is none (treat the text as a search query).
const ASSET_ID_RE = /[0-9a-fA-F]{16}/

export function extractModId(raw) {
  const m = ASSET_ID_RE.exec(raw || '')
  return m ? m[0].toUpperCase() : null
}

// Every mod id in the text, deduplicated, in the order written — so a
// comma-separated list of ids (or several pasted Workshop URLs) can be added
// in one go (#104).
export function extractModIds(raw) {
  const found = (raw || '').match(new RegExp(ASSET_ID_RE, 'g')) || []
  return [...new Set(found.map((id) => id.toUpperCase()))]
}

export function normalizeMod(m) {
  return {
    modId: m.modId,
    name: m.name ?? null,
    version: m.version ?? null,
    versions: Array.isArray(m.versions) ? m.versions.filter(Boolean) : [],
    explicit: m.explicit ?? true,
    from_scenario: m.from_scenario ?? false,
    provides_scenarios: m.provides_scenarios ?? false,
    dependencies: Array.isArray(m.dependencies) ? m.dependencies.filter(Boolean) : [],
    // When this mod was added, as a sequence number (#105). null = added before
    // the counter existed; those sort as oldest, keeping their current order.
    added_order: Number.isInteger(m.added_order) ? m.added_order : null,
  }
}

export function normalizeMods(mods) {
  return (mods || []).map(normalizeMod)
}

// modIds that must remain: every explicit mod plus everything transitively
// required by an explicit mod.
export function neededSet(mods) {
  const byId = new Map(mods.map((m) => [m.modId, m]))
  const needed = new Set()
  const stack = mods.filter((m) => m.explicit).map((m) => m.modId)
  while (stack.length) {
    const id = stack.pop()
    if (needed.has(id)) continue
    needed.add(id)
    const mod = byId.get(id)
    if (mod) for (const d of mod.dependencies) if (byId.has(d) && !needed.has(d)) stack.push(d)
  }
  return needed
}

// Explicit mods (other than `exceptId`) that transitively require `targetId`.
export function requiredBy(mods, targetId, exceptId = null) {
  const byId = new Map(mods.map((m) => [m.modId, m]))
  const out = []
  for (const root of mods) {
    if (!root.explicit || root.modId === exceptId || root.modId === targetId) continue
    const seen = new Set()
    const stack = [...root.dependencies]
    let reaches = false
    while (stack.length) {
      const id = stack.pop()
      if (id === targetId) { reaches = true; break }
      if (seen.has(id)) continue
      seen.add(id)
      const m = byId.get(id)
      if (m) stack.push(...m.dependencies)
    }
    if (reaches) out.push(root)
  }
  return out
}

// Would `id` still be required (by some other explicit mod) if it stopped being
// explicit itself? If so, "removing" it should only demote it to a dependency.
export function stillRequiredWithoutExplicit(mods, id) {
  const temp = mods.map((m) =>
    m.modId === id ? { ...m, explicit: false, from_scenario: false } : m,
  )
  return neededSet(temp).has(id)
}

// Dependencies that become unused once `removeId` is gone.
export function orphansAfterRemoving(mods, removeId) {
  const remaining = mods.filter((m) => m.modId !== removeId)
  const needed = neededSet(remaining)
  return remaining.filter((m) => !m.explicit && !needed.has(m.modId))
}

// Drop any dependency-only mod no longer required by an explicit mod.
export function pruneOrphans(mods) {
  const needed = neededSet(mods)
  return mods.filter((m) => m.explicit || needed.has(m.modId))
}

// Remove the mods that came from the previously selected scenario, then prune
// dependencies they leave orphaned (shared deps required by user mods survive).
export function clearScenarioMods(mods) {
  return pruneOrphans(mods.filter((m) => !m.from_scenario))
}

// Merge a resolved add ({ root, mods:[{modId,name,version,versions,
// provides_scenarios,dependencies}] })
// into the current list. The resolved root becomes explicit; the rest are added
// as dependencies unless already present (an existing explicit mod stays
// explicit). The resolved current version is deliberately NOT written into
// `version` — that field is a user lock (#60) and defaults to "follow latest".
export function mergeResolved(current, resolved, { fromScenario = false } = {}) {
  const byId = new Map(current.map((m) => [m.modId, { ...m }]))
  const rootId = resolved.root
  let nextOrder = nextAddedOrder(current)
  for (const rm of resolved.mods || []) {
    const isRoot = rm.modId === rootId
    const existing = byId.get(rm.modId)
    if (existing) {
      if (rm.name) existing.name = rm.name
      if (rm.versions && rm.versions.length) existing.versions = rm.versions
      if (rm.dependencies && rm.dependencies.length) existing.dependencies = rm.dependencies
      if (rm.provides_scenarios != null) existing.provides_scenarios = rm.provides_scenarios
      if (isRoot) existing.explicit = true
      if (isRoot && fromScenario) existing.from_scenario = true
    } else {
      byId.set(rm.modId, {
        modId: rm.modId,
        name: rm.name ?? null,
        version: null,
        versions: rm.versions || [],
        explicit: isRoot,
        from_scenario: isRoot && fromScenario,
        provides_scenarios: rm.provides_scenarios ?? false,
        dependencies: rm.dependencies || [],
        added_order: nextOrder++,
      })
    }
  }
  return [...byId.values()]
}

// The sequence number the next added mod should get (#105).
function nextAddedOrder(mods) {
  return Math.max(0, ...mods.map((m) => m.added_order ?? 0)) + 1
}

// ---- Sorting (#105, #164) --------------------------------------------------
// Every sort here reorders the WHOLE list — since #164 a dependency may sit
// anywhere, including above the mod that pulled it in — and all of them are
// real reorders: what you see is the order config.json gets.

export function sortModsByName(mods) {
  return [...mods].sort((a, b) =>
    (a.name || a.modId).localeCompare(b.name || b.modId, undefined, { sensitivity: 'base' }),
  )
}

export function sortModsByAdded(mods) {
  // Mods from before the counter existed have no number: treat them as oldest
  // and keep their relative order (sort is stable).
  return [...mods].sort((a, b) => (a.added_order ?? 0) - (b.added_order ?? 0))
}

// The order rendered into config.json's mods[] — since #164 simply the order of
// the list, because the list is now fully reorderable (drag, AI, sorts).
export function orderedMods(mods) {
  return [...mods]
}

// Explicit picks first, dependencies after: how the list was ALWAYS rendered
// before #164, whatever order the array happened to be stored in. Applied once
// when an existing template or a mods file is loaded, so opening a template in
// the new list shows exactly the order it has been running with — and saving it
// untouched writes back the same config.json.
export function partitionOrder(mods) {
  return [...mods.filter((m) => m.explicit), ...mods.filter((m) => !m.explicit)]
}

// ---- Manual reordering (#164) ----------------------------------------------

// Move one mod one step (dir -1 up, +1 down). Returns a new array; out-of-range
// moves return the list unchanged.
export function moveMod(mods, id, dir) {
  const i = mods.findIndex((m) => m.modId === id)
  const j = i + dir
  if (i < 0 || j < 0 || j >= mods.length) return mods
  const out = [...mods]
  ;[out[i], out[j]] = [out[j], out[i]]
  return out
}

// Drag and drop: lift the mod at `from` and drop it at index `to`. `to` is the
// index the mod ends up at in the resulting list.
export function reorderMods(mods, from, to) {
  if (from === to || from < 0 || from >= mods.length) return mods
  const out = [...mods]
  const [moved] = out.splice(from, 1)
  out.splice(Math.max(0, Math.min(to, out.length)), 0, moved)
  return out
}

// ---- Dependency-aware ordering (#164) --------------------------------------
// Community guidance for Reforger is that a mod should be listed after what it
// requires, with mods that patch other mods last. Whether the engine honours
// the array order at all is not something we can verify (see the release notes),
// so this is offered as a choice, never applied on its own.

// Pairs where a mod is listed BEFORE something it depends on.
export function dependencyViolations(mods) {
  const pos = new Map(mods.map((m, i) => [m.modId, i]))
  const out = []
  for (const m of mods) {
    for (const d of m.dependencies) {
      if (!pos.has(d)) continue // dependency isn't in this list at all
      if (pos.get(d) > pos.get(m.modId)) out.push({ mod: m.modId, dependency: d })
    }
  }
  return out
}

// Stable topological order: every mod after the mods it requires, and otherwise
// as close to the current order as that allows. A dependency cycle cannot be
// resolved, so the mods in it keep their current relative order rather than
// being dropped — the list must always come back whole.
export function topoOrder(mods) {
  const index = new Map(mods.map((m, i) => [m.modId, i]))
  const remaining = new Set(mods.map((m) => m.modId))
  const placed = new Set()
  const out = []
  while (remaining.size) {
    // Ready = everything whose dependencies are already placed, taken in the
    // list's current order so an unforced choice never shuffles anything.
    const ready = mods.filter(
      (m) =>
        remaining.has(m.modId) &&
        m.dependencies.every((d) => !index.has(d) || placed.has(d)),
    )
    // Nothing ready means a cycle: break it on the earliest mod still left.
    const batch = ready.length ? ready : [mods.find((m) => remaining.has(m.modId))]
    for (const m of batch) {
      out.push(m)
      placed.add(m.modId)
      remaining.delete(m.modId)
    }
  }
  return out
}

// ---- Applying an order that came from outside (#164) ------------------------

// Reorder `mods` to follow `ids` (an order proposed by an AI, or read off a
// pasted list). Nothing is ever added or removed by this:
//   * ids that aren't in the list are reported as `unknown` and ignored;
//   * mods the order never mentions are reported as `missing` and slotted back
//     in at the position they hold today, rather than being swept to the end.
export function applyOrderIds(mods, ids) {
  const byId = new Map(mods.map((m) => [m.modId, m]))
  const seen = new Set()
  const unknown = []
  const ordered = []
  for (const raw of ids) {
    const id = (raw || '').toUpperCase()
    if (seen.has(id)) continue // a repeat later in the reply is not a second mod
    seen.add(id)
    if (!byId.has(id)) {
      unknown.push(id)
      continue
    }
    ordered.push(byId.get(id))
  }
  const missing = mods.filter((m) => !seen.has(m.modId))
  for (const m of missing) {
    const at = mods.indexOf(m)
    ordered.splice(Math.min(at, ordered.length), 0, m)
  }
  return {
    mods: ordered,
    missing: missing.map((m) => m.modId),
    unknown,
  }
}

// ---- Loading a mod template into a template's mod list (#166) ---------------

// The mods that must survive a "replace": the mod backing the selected scenario
// and whatever it transitively requires. A server runs its scenario — loading a
// mod list can add, drop and reorder everything else, but it may not quietly
// take the scenario's own mod away (the wizard refuses that by hand too).
function scenarioKeepSet(mods) {
  const asRoots = mods.map((m) => ({ ...m, explicit: !!m.from_scenario }))
  return mods.some((m) => m.from_scenario) ? neededSet(asRoots) : new Set()
}

// Load a mod template's list into the mods of a server template.
//
//   replace=false ("Add")     — existing mods stay exactly where they are and
//                               the new ones are appended in the mod template's
//                               order.
//   replace=true  ("Replace") — everything except the scenario's mods goes, and
//                               the mod template's order becomes the list.
//
// A mod already on the list is never duplicated: it is marked as an explicit
// pick and takes the mod template's version lock, since that lock is part of the
// set the user chose to load. Returns the new list plus what happened, so the
// wizard can show it before anything is saved.
export function applyModTemplate(current, incoming, { replace = false } = {}) {
  const mods = normalizeMods(current)
  const list = normalizeMods(incoming).map((m) => ({ ...m, explicit: true }))
  const keep = replace ? scenarioKeepSet(mods) : new Set(mods.map((m) => m.modId))
  const base = mods.filter((m) => keep.has(m.modId))
  const removed = mods.filter((m) => !keep.has(m.modId)).map((m) => m.modId)

  const byId = new Map(base.map((m) => [m.modId, { ...m }]))
  const out = base.map((m) => byId.get(m.modId))
  const added = []
  const relocked = []
  let nextOrder = nextAddedOrder(mods)

  for (const inc of list) {
    const existing = byId.get(inc.modId)
    if (existing) {
      if (existing.version !== inc.version) {
        relocked.push({ modId: inc.modId, from: existing.version, to: inc.version })
      }
      existing.explicit = true
      existing.version = inc.version
      if (inc.name) existing.name = inc.name
      if (inc.versions.length) existing.versions = inc.versions
      continue
    }
    const entry = { ...inc, added_order: nextOrder++ }
    byId.set(entry.modId, entry)
    out.push(entry)
    added.push(entry.modId)
  }

  return { mods: out, added, removed, relocked }
}

// The mods whose position changed between two orders, for the preview.
export function movedMods(before, after) {
  const was = new Map(before.map((m, i) => [m.modId, i]))
  return after
    .map((m, i) => ({ mod: m, from: was.get(m.modId) ?? -1, to: i }))
    .filter((r) => r.from !== r.to)
}
