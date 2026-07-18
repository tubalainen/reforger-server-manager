import { describe, expect, it } from 'vitest'

import {
  clearScenarioMods,
  extractModId,
  extractModIds,
  mergeResolved,
  neededSet,
  normalizeMod,
  orderedMods,
  orphansAfterRemoving,
  pruneOrphans,
  requiredBy,
  sortModsByAdded,
  sortModsByName,
  stillRequiredWithoutExplicit,
} from '../mods'

describe('extractModId', () => {
  it('pulls a 16-hex id out of a bare id, "{id}-slug" or a full URL', () => {
    expect(extractModId('59D64ADD6FC59CBF')).toBe('59D64ADD6FC59CBF')
    expect(extractModId('59d64add6fc59cbf-projectredline-uh-60')).toBe('59D64ADD6FC59CBF')
    expect(
      extractModId('https://reforger.armaplatform.com/workshop/59D64ADD6FC59CBF-ProjectRedline-UH-60'),
    ).toBe('59D64ADD6FC59CBF')
  })

  it('returns null for free-text so the caller treats it as a search query', () => {
    expect(extractModId('Project Redline')).toBe(null)
    expect(extractModId('')).toBe(null)
    expect(extractModId(null)).toBe(null)
  })
})

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

describe('extractModIds (#104)', () => {
  it('pulls every id out of a comma-separated list, upper-cased', () => {
    expect(extractModIds('1337C0DE5DABBEEF, badc0dedabbeda5e, 595F2BF2F44836FB')).toEqual([
      '1337C0DE5DABBEEF',
      'BADC0DEDABBEDA5E',
      '595F2BF2F44836FB',
    ])
  })

  it('copes with mixed URLs and ids, and deduplicates', () => {
    expect(
      extractModIds(
        'https://reforger.armaplatform.com/workshop/59D64ADD6FC59CBF-UH-60, 1337C0DE5DABBEEF, 59d64add6fc59cbf',
      ),
    ).toEqual(['59D64ADD6FC59CBF', '1337C0DE5DABBEEF'])
  })

  it('returns [] for free-text so the caller searches instead', () => {
    expect(extractModIds('Project Redline')).toEqual([])
    expect(extractModIds('')).toEqual([])
    expect(extractModIds(null)).toEqual([])
  })
})

describe('added_order (#105)', () => {
  it('stamps new mods with an increasing counter on merge', () => {
    let mods = mergeResolved([], { root: 'A', mods: [{ modId: 'A' }] })
    mods = mergeResolved(mods, { root: 'B', mods: [{ modId: 'B' }] })
    const byId = Object.fromEntries(mods.map((m) => [m.modId, m.added_order]))
    expect(byId.A).toBe(1)
    expect(byId.B).toBe(2)
  })

  it('never renumbers a mod that is merged again', () => {
    let mods = mergeResolved([], { root: 'A', mods: [{ modId: 'A' }] })
    mods = mergeResolved(mods, { root: 'B', mods: [{ modId: 'B' }] })
    mods = mergeResolved(mods, { root: 'A', mods: [{ modId: 'A' }] }) // re-add
    expect(mods.find((m) => m.modId === 'A').added_order).toBe(1)
  })

  it('counts on from the highest existing number, ignoring null legacy rows', () => {
    const current = [mod('OLD', { added_order: null }), mod('X', { added_order: 7 })]
    const merged = mergeResolved(current, { root: 'Y', mods: [{ modId: 'Y' }] })
    expect(merged.find((m) => m.modId === 'Y').added_order).toBe(8)
  })

  it('survives normalizeMod, so it round-trips through save and JSON export', () => {
    expect(normalizeMod({ modId: 'A', added_order: 3 }).added_order).toBe(3)
    expect(normalizeMod({ modId: 'A' }).added_order).toBe(null)
    expect(normalizeMod({ modId: 'A', added_order: 'x' }).added_order).toBe(null)
  })
})

describe('sorting (#105)', () => {
  const list = () => [
    mod('B1', { name: 'bravo', explicit: true, added_order: 2 }),
    mod('A1', { name: 'Alpha', explicit: true, added_order: 3 }),
    mod('C1', { name: 'Charlie', explicit: true, added_order: 1 }),
    mod('D1', { name: 'zz-dep', explicit: false }),
  ]

  it('sortModsByName orders the explicit tier case-insensitively, deps stay after', () => {
    expect(sortModsByName(list()).map((m) => m.modId)).toEqual(['A1', 'B1', 'C1', 'D1'])
  })

  it('sortModsByName falls back to the modId when a mod has no name yet', () => {
    const mods = [
      mod('BBBBBBBBBBBBBBBB', { name: null, explicit: true }),
      mod('A1', { name: 'zulu', explicit: true }),
    ]
    expect(sortModsByName(mods).map((m) => m.modId)).toEqual(['BBBBBBBBBBBBBBBB', 'A1'])
  })

  it('sortModsByAdded restores the add order after a name sort', () => {
    const sorted = sortModsByAdded(sortModsByName(list()))
    expect(sorted.map((m) => m.modId)).toEqual(['C1', 'B1', 'A1', 'D1'])
  })

  it('sortModsByAdded keeps legacy un-numbered mods first, in their current order', () => {
    const mods = [
      mod('N1', { added_order: 5, explicit: true }),
      mod('L1', { added_order: null, explicit: true }),
      mod('L2', { added_order: null, explicit: true }),
    ]
    expect(sortModsByAdded(mods).map((m) => m.modId)).toEqual(['L1', 'L2', 'N1'])
  })
})
