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

export const MODS_FILE_FORMAT = 'reforger-server-manager/mods@1'

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

// ---- Sorting (#105) --------------------------------------------------------
// Both sorts reorder the explicit tier only — dependencies always render (and
// export) after it, per orderedMods — and both are real reorders: what you see
// is the order config.json gets.

export function sortModsByName(mods) {
  const explicit = mods.filter((m) => m.explicit)
  explicit.sort((a, b) =>
    (a.name || a.modId).localeCompare(b.name || b.modId, undefined, { sensitivity: 'base' }),
  )
  return [...explicit, ...mods.filter((m) => !m.explicit)]
}

export function sortModsByAdded(mods) {
  const explicit = mods.filter((m) => m.explicit)
  // Mods from before the counter existed have no number: treat them as oldest
  // and keep their relative order (sort is stable).
  explicit.sort((a, b) => (a.added_order ?? 0) - (b.added_order ?? 0))
  return [...explicit, ...mods.filter((m) => !m.explicit)]
}

// Keep explicit mods in their chosen order, dependencies after them — this is
// the order rendered into config.json's mods[].
export function orderedMods(mods) {
  return [...mods.filter((m) => m.explicit), ...mods.filter((m) => !m.explicit)]
}
