import { describe, expect, it } from 'vitest'

import {
  clearScenarioMods,
  mergeResolved,
  neededSet,
  normalizeMod,
  orderedMods,
  orphansAfterRemoving,
  pruneOrphans,
  requiredBy,
  stillRequiredWithoutExplicit,
} from '../mods'

// The mod dependency graph is the most intricate logic in the frontend and had no
// tests at all. Shape: A (explicit) -> B -> C, and D (explicit) -> C  (C shared).
const mod = (modId, over = {}) => normalizeMod({ modId, ...over })

const graph = () => [
  mod('A', { explicit: true, dependencies: ['B'] }),
  mod('B', { explicit: false, dependencies: ['C'] }),
  mod('C', { explicit: false, dependencies: [] }),
  mod('D', { explicit: true, dependencies: ['C'] }),
]

describe('neededSet', () => {
  it('keeps every explicit mod and everything they transitively require', () => {
    expect([...neededSet(graph())].sort()).toEqual(['A', 'B', 'C', 'D'])
  })

  it('does not keep a dependency nothing points at any more', () => {
    const orphaned = [mod('A', { explicit: true }), mod('B', { explicit: false })]
    expect(neededSet(orphaned).has('B')).toBe(false)
  })

  it('survives a dependency cycle instead of hanging', () => {
    const cyclic = [
      mod('A', { explicit: true, dependencies: ['B'] }),
      mod('B', { explicit: false, dependencies: ['A'] }),
    ]
    expect([...neededSet(cyclic)].sort()).toEqual(['A', 'B'])
  })
})

describe('requiredBy', () => {
  it('names the explicit mods that transitively pull a mod in', () => {
    expect(requiredBy(graph(), 'C').map((m) => m.modId).sort()).toEqual(['A', 'D'])
    expect(requiredBy(graph(), 'B').map((m) => m.modId)).toEqual(['A'])
  })

  it('can exclude the mod being removed from the reckoning', () => {
    expect(requiredBy(graph(), 'C', 'D').map((m) => m.modId)).toEqual(['A'])
  })
})

describe('removing a mod', () => {
  it('demotes rather than deletes when another explicit mod still needs it', () => {
    // D is explicit and also required by nothing else... but C is shared: dropping
    // D as an explicit pick must not take C away from A.
    expect(stillRequiredWithoutExplicit(graph(), 'D')).toBe(false)
    const shared = [...graph(), mod('E', { explicit: true, dependencies: ['D'] })]
    expect(stillRequiredWithoutExplicit(shared, 'D')).toBe(true)
  })

  it('reports only the dependencies that are actually orphaned', () => {
    // Removing A orphans B (only A needed it) but NOT C (D still does).
    expect(orphansAfterRemoving(graph(), 'A').map((m) => m.modId)).toEqual(['B'])
    expect(orphansAfterRemoving(graph(), 'D').map((m) => m.modId)).toEqual([])
  })
})

describe('pruneOrphans', () => {
  it('drops unreferenced dependencies and keeps shared ones', () => {
    const afterRemovingA = graph().filter((m) => m.modId !== 'A')
    expect(pruneOrphans(afterRemovingA).map((m) => m.modId).sort()).toEqual(['C', 'D'])
  })
})

describe('clearScenarioMods', () => {
  it("removes the scenario's mods but keeps what a user mod still needs", () => {
    const mods = [
      mod('SCEN', { explicit: true, from_scenario: true, dependencies: ['SHARED'] }),
      mod('SHARED', { explicit: false }),
      mod('USER', { explicit: true, dependencies: ['SHARED'] }),
    ]
    const left = clearScenarioMods(mods).map((m) => m.modId).sort()
    expect(left).toEqual(['SHARED', 'USER']) // SHARED survives: USER still needs it
  })

  it("takes the scenario's private dependencies with it", () => {
    const mods = [
      mod('SCEN', { explicit: true, from_scenario: true, dependencies: ['PRIV'] }),
      mod('PRIV', { explicit: false }),
      mod('USER', { explicit: true }),
    ]
    expect(clearScenarioMods(mods).map((m) => m.modId)).toEqual(['USER'])
  })
})

describe('mergeResolved', () => {
  const resolved = {
    root: 'R',
    mods: [
      { modId: 'R', name: 'Root', versions: ['2.0', '1.0'], dependencies: ['DEP'] },
      { modId: 'DEP', name: 'Dep', versions: [], dependencies: [] },
    ],
  }

  it('adds the root as explicit and its tree as dependencies', () => {
    const out = mergeResolved([], resolved)
    expect(out.find((m) => m.modId === 'R').explicit).toBe(true)
    expect(out.find((m) => m.modId === 'DEP').explicit).toBe(false)
  })

  it('never writes the Workshop version into the user lock (#60)', () => {
    // `version` means "the user pinned this"; null = follow the latest release.
    const out = mergeResolved([], resolved)
    expect(out.every((m) => m.version === null)).toBe(true)
    expect(out.find((m) => m.modId === 'R').versions).toEqual(['2.0', '1.0'])
  })

  it('keeps an existing user lock and an existing explicit flag', () => {
    const current = [mod('DEP', { explicit: true, version: '1.2.3' })]
    const out = mergeResolved(current, resolved)
    const dep = out.find((m) => m.modId === 'DEP')
    expect(dep.explicit).toBe(true) // was the user's own pick: not demoted
    expect(dep.version).toBe('1.2.3') // lock preserved
  })

  it('flags the scenario mod only when adding a scenario', () => {
    expect(mergeResolved([], resolved, { fromScenario: true })
      .find((m) => m.modId === 'R').from_scenario).toBe(true)
    expect(mergeResolved([], resolved)
      .find((m) => m.modId === 'R').from_scenario).toBe(false)
  })
})

describe('orderedMods', () => {
  it('puts explicit mods first — config.json mod order is load order', () => {
    expect(orderedMods(graph()).map((m) => m.modId)).toEqual(['A', 'D', 'B', 'C'])
  })
})
