// Pure helpers for the mod dependency graph (issue #55).
//
// A mod entry is { modId, name, version, explicit, from_scenario, dependencies }
// where `explicit` marks a user/scenario-chosen "root", `dependencies` are the
// direct dependency modIds (graph edges), and `from_scenario` flags the mod that
// backs the currently selected scenario. These live only in the manager; the
// server's config.json gets the flat modId/name/version list.

export const MODS_FILE_FORMAT = 'reforger-server-manager/mods@1'

export function normalizeMod(m) {
  return {
    modId: m.modId,
    name: m.name ?? null,
    version: m.version ?? null,
    explicit: m.explicit ?? true,
    from_scenario: m.from_scenario ?? false,
    dependencies: Array.isArray(m.dependencies) ? m.dependencies.filter(Boolean) : [],
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

// Merge a resolved add ({ root, mods:[{modId,name,version,dependencies}] }) into
// the current list. The resolved root becomes explicit; the rest are added as
// dependencies unless already present (an existing explicit mod stays explicit).
export function mergeResolved(current, resolved, { fromScenario = false } = {}) {
  const byId = new Map(current.map((m) => [m.modId, { ...m }]))
  const rootId = resolved.root
  for (const rm of resolved.mods || []) {
    const isRoot = rm.modId === rootId
    const existing = byId.get(rm.modId)
    if (existing) {
      if (rm.name) existing.name = rm.name
      if (rm.version) existing.version = rm.version
      if (rm.dependencies && rm.dependencies.length) existing.dependencies = rm.dependencies
      if (isRoot) existing.explicit = true
      if (isRoot && fromScenario) existing.from_scenario = true
    } else {
      byId.set(rm.modId, {
        modId: rm.modId,
        name: rm.name ?? null,
        version: rm.version ?? null,
        explicit: isRoot,
        from_scenario: isRoot && fromScenario,
        dependencies: rm.dependencies || [],
      })
    }
  }
  return [...byId.values()]
}

// Keep explicit mods in their chosen order, dependencies after them — this is
// the order rendered into config.json's mods[].
export function orderedMods(mods) {
  return [...mods.filter((m) => m.explicit), ...mods.filter((m) => !m.explicit)]
}
