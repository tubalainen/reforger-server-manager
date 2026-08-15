import { describe, expect, it } from 'vitest'

import {
  adoptEditedMods,
  applyModTemplate,
  applyOrderIds,
  clearScenarioMods,
  dependencyViolations,
  extractModId,
  extractModIds,
  mergeResolved,
  moveMod,
  movedMods,
  neededSet,
  normalizeMod,
  orderedMods,
  orphansAfterRemoving,
  partitionOrder,
  pruneOrphans,
  reorderMods,
  requiredBy,
  sortModsByAdded,
  sortModsByName,
  stillRequiredWithoutExplicit,
  topoOrder,
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
  it('is the list itself — since #164 the order you see is the order exported', () => {
    expect(orderedMods(graph()).map((m) => m.modId)).toEqual(['A', 'B', 'C', 'D'])
  })

  it('does not alias the input, so exporting cannot mutate the template', () => {
    const mods = graph()
    expect(orderedMods(mods)).not.toBe(mods)
  })
})

describe('partitionOrder (#164 migration)', () => {
  it('reproduces the pre-#164 render order: picks first, dependencies after', () => {
    expect(partitionOrder(graph()).map((m) => m.modId)).toEqual(['A', 'D', 'B', 'C'])
  })

  it('is what keeps an untouched old template rendering the same config.json', () => {
    // Whatever order the array was stored in, the list used to render (and
    // export) partitioned — so loading through partitionOrder is a no-op change.
    const stored = [
      mod('DEP', { explicit: false }),
      mod('PICK', { explicit: true }),
    ]
    expect(partitionOrder(stored).map((m) => m.modId)).toEqual(['PICK', 'DEP'])
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

describe('sorting (#105, #164)', () => {
  const list = () => [
    mod('B1', { name: 'bravo', explicit: true, added_order: 2 }),
    mod('A1', { name: 'Alpha', explicit: true, added_order: 3 }),
    mod('C1', { name: 'Charlie', explicit: true, added_order: 1 }),
    mod('D1', { name: 'zz-dep', explicit: false, added_order: 4 }),
  ]

  it('sortModsByName orders the whole list case-insensitively', () => {
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

  it('sorts dependencies in among the picks — since #164 they are one list', () => {
    const mods = [
      mod('PICK', { name: 'zulu', explicit: true, added_order: 2 }),
      mod('DEP', { name: 'alpha', explicit: false, added_order: 1 }),
    ]
    expect(sortModsByName(mods).map((m) => m.modId)).toEqual(['DEP', 'PICK'])
    expect(sortModsByAdded(mods).map((m) => m.modId)).toEqual(['DEP', 'PICK'])
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

// ---- #164: the list is the load order --------------------------------------

describe('moveMod / reorderMods', () => {
  const list = () => [mod('A'), mod('B'), mod('C')]

  it('swaps a mod with its neighbour', () => {
    expect(moveMod(list(), 'C', -1).map((m) => m.modId)).toEqual(['A', 'C', 'B'])
    expect(moveMod(list(), 'A', 1).map((m) => m.modId)).toEqual(['B', 'A', 'C'])
  })

  it('refuses to move off either end, or to move a mod that is not there', () => {
    expect(moveMod(list(), 'A', -1).map((m) => m.modId)).toEqual(['A', 'B', 'C'])
    expect(moveMod(list(), 'C', 1).map((m) => m.modId)).toEqual(['A', 'B', 'C'])
    expect(moveMod(list(), 'ZZ', 1).map((m) => m.modId)).toEqual(['A', 'B', 'C'])
  })

  it('drops a dragged mod at the index it was dropped on, in both directions', () => {
    expect(reorderMods(list(), 0, 2).map((m) => m.modId)).toEqual(['B', 'C', 'A'])
    expect(reorderMods(list(), 2, 0).map((m) => m.modId)).toEqual(['C', 'A', 'B'])
  })

  it('never loses or duplicates a mod, whatever it is handed', () => {
    for (const [from, to] of [[0, 0], [1, 9], [-1, 1], [2, -5]]) {
      const out = reorderMods(list(), from, to)
      expect([...out.map((m) => m.modId)].sort()).toEqual(['A', 'B', 'C'])
    }
  })
})

describe('dependencyViolations / topoOrder', () => {
  // A needs B, B needs C, D needs C.
  const chain = () => [
    mod('A', { explicit: true, dependencies: ['B'] }),
    mod('B', { explicit: false, dependencies: ['C'] }),
    mod('C', { explicit: false }),
    mod('D', { explicit: true, dependencies: ['C'] }),
  ]

  it('names every mod listed before something it requires, and only those', () => {
    // D sits after C, so D is fine; A and B are both above what they need.
    expect(dependencyViolations(chain())).toEqual([
      { mod: 'A', dependency: 'B' },
      { mod: 'B', dependency: 'C' },
    ])
  })

  it('says nothing about a dependency that is not in the list', () => {
    expect(dependencyViolations([mod('A', { dependencies: ['GONE'] })])).toEqual([])
  })

  it('topoOrder puts every mod after what it requires', () => {
    const sorted = topoOrder(chain())
    expect(dependencyViolations(sorted)).toEqual([])
    expect(sorted.map((m) => m.modId)).toEqual(['C', 'B', 'D', 'A'])
  })

  it('topoOrder leaves an order that is already correct exactly as it is', () => {
    const already = topoOrder(chain())
    expect(topoOrder(already).map((m) => m.modId)).toEqual(already.map((m) => m.modId))
  })

  it('topoOrder is stable: unrelated mods keep their relative order', () => {
    const mods = [mod('X'), mod('Y'), mod('Z')]
    expect(topoOrder(mods).map((m) => m.modId)).toEqual(['X', 'Y', 'Z'])
  })

  it('topoOrder returns the whole list even when the graph has a cycle', () => {
    const cyclic = [
      mod('A', { dependencies: ['B'] }),
      mod('B', { dependencies: ['A'] }),
      mod('C'),
    ]
    expect(topoOrder(cyclic).map((m) => m.modId).sort()).toEqual(['A', 'B', 'C'])
  })
})

describe('applyOrderIds (an order proposed by an AI or pasted in)', () => {
  const list = () => [mod('A1'), mod('B1'), mod('C1')]

  it('reorders to match the ids given', () => {
    const out = applyOrderIds(list(), ['C1', 'A1', 'B1'])
    expect(out.mods.map((m) => m.modId)).toEqual(['C1', 'A1', 'B1'])
    expect(out.missing).toEqual([])
    expect(out.unknown).toEqual([])
  })

  it('accepts lower-case ids, because a model will happily re-type them', () => {
    expect(applyOrderIds(list(), ['c1', 'a1', 'b1']).mods.map((m) => m.modId)).toEqual(
      ['C1', 'A1', 'B1'],
    )
  })

  it('ignores an id that is not in the template and reports it', () => {
    const out = applyOrderIds(list(), ['C1', 'DEADBEEFDEADBEEF', 'A1', 'B1'])
    expect(out.mods.map((m) => m.modId)).toEqual(['C1', 'A1', 'B1'])
    expect(out.unknown).toEqual(['DEADBEEFDEADBEEF'])
  })

  it('keeps a mod the answer forgot, near where it already was', () => {
    const out = applyOrderIds(list(), ['C1', 'A1']) // B1 (index 1) never mentioned
    expect(out.mods.map((m) => m.modId)).toEqual(['C1', 'B1', 'A1'])
    expect(out.missing).toEqual(['B1'])
  })

  it('treats a repeated id as one mod rather than duplicating it', () => {
    const out = applyOrderIds(list(), ['C1', 'A1', 'C1', 'B1'])
    expect(out.mods.map((m) => m.modId)).toEqual(['C1', 'A1', 'B1'])
  })

  it('never changes which mods are in the list', () => {
    const before = list()
    for (const ids of [[], ['ZZZZZZZZZZZZZZZZ'], ['B1'], ['C1', 'B1', 'A1']]) {
      const out = applyOrderIds(before, ids)
      expect([...out.mods.map((m) => m.modId)].sort()).toEqual(['A1', 'B1', 'C1'])
    }
  })
})

describe('reading a mod order out of a real AI reply', () => {
  // The parser is extractModIds: whatever shape the answer arrives in, the ids
  // in it, in order, are the answer. These are the shapes models actually emit.
  const ids = ['595F2BF2F44836FB', '1337C0DE5DABBEEF', 'BADC0DEDABBEDA5E']
  const mods = ids.map((id) => mod(id))
  const expectOrder = (text, want) =>
    expect(applyOrderIds(mods, extractModIds(text)).mods.map((m) => m.modId)).toEqual(want)

  it('reads the format the prompt asks for', () => {
    expectOrder(
      `BADC0DEDABBEDA5E | Core Lib | framework, everything builds on it
1337C0DE5DABBEEF | Weapons | content pack
595F2BF2F44836FB | Tweaks | patches the others, goes last`,
      ['BADC0DEDABBEDA5E', '1337C0DE5DABBEEF', '595F2BF2F44836FB'],
    )
  })

  it('reads a numbered list with a preamble the model was told not to write', () => {
    expectOrder(
      `Sure! Here is the recommended load order:

1. BADC0DEDABBEDA5E (Core Lib)
2. 595F2BF2F44836FB (Tweaks)
3. 1337C0DE5DABBEEF (Weapons)`,
      ['BADC0DEDABBEDA5E', '595F2BF2F44836FB', '1337C0DE5DABBEEF'],
    )
  })

  it('reads a fenced JSON array', () => {
    expectOrder(
      '```json\n["1337c0de5dabbeef", "badc0dedabbeda5e", "595f2bf2f44836fb"]\n```',
      ['1337C0DE5DABBEEF', 'BADC0DEDABBEDA5E', '595F2BF2F44836FB'],
    )
  })

  it('reads a markdown table', () => {
    expectOrder(
      `| # | Mod | Id |
|---|-----|----|
| 1 | Core Lib | BADC0DEDABBEDA5E |
| 2 | Weapons | 1337C0DE5DABBEEF |
| 3 | Tweaks | 595F2BF2F44836FB |`,
      ['BADC0DEDABBEDA5E', '1337C0DE5DABBEEF', '595F2BF2F44836FB'],
    )
  })

  it('ignores trailing remarks that mention a mod again', () => {
    // The first mention wins, so a closing "note that BADC… is the framework"
    // cannot promote a mod that was listed last.
    expectOrder(
      `1337C0DE5DABBEEF
BADC0DEDABBEDA5E
595F2BF2F44836FB

Note: 595F2BF2F44836FB patches 1337C0DE5DABBEEF, which is why it is last.`,
      ['1337C0DE5DABBEEF', 'BADC0DEDABBEDA5E', '595F2BF2F44836FB'],
    )
  })

  it('finds nothing to do in an answer with no ids at all', () => {
    expect(extractModIds('I cannot help with that.')).toEqual([])
  })
})

describe('movedMods', () => {
  it('reports only the mods whose position changed', () => {
    const before = [mod('A'), mod('B'), mod('C')]
    const after = [mod('C'), mod('A'), mod('B')]
    expect(movedMods(before, after).map((r) => [r.mod.modId, r.from, r.to])).toEqual([
      ['C', 2, 0],
      ['A', 0, 1],
      ['B', 1, 2],
    ])
  })

  it('reports nothing when the order is unchanged', () => {
    const mods = [mod('A'), mod('B')]
    expect(movedMods(mods, [...mods])).toEqual([])
  })
})

describe('applyModTemplate (#166)', () => {
  // A template mid-edit: the scenario's own mod S with its dependency SD, plus
  // an addon A the user picked themselves.
  const current = () => [
    mod('S', { explicit: true, from_scenario: true, dependencies: ['SD'] }),
    mod('SD', { explicit: false }),
    mod('A', { explicit: true, version: '1.0' }),
  ]
  // A mod template, in the order the user arranged it.
  const shelf = () => [mod('X'), mod('A', { version: '2.0' }), mod('Y')]

  it('adds the missing mods in the mod template\'s order, keeping the list', () => {
    const out = applyModTemplate(current(), shelf())
    expect(out.mods.map((m) => m.modId)).toEqual(['S', 'SD', 'A', 'X', 'Y'])
    expect(out.added).toEqual(['X', 'Y'])
    expect(out.removed).toEqual([])
  })

  it('never duplicates a mod that is already there, but re-locks its version', () => {
    const out = applyModTemplate(current(), shelf())
    const a = out.mods.find((m) => m.modId === 'A')
    expect(a.version).toBe('2.0')
    expect(out.relocked).toEqual([{ modId: 'A', from: '1.0', to: '2.0' }])
  })

  it('marks a mod that was only a dependency as an explicit pick when loaded', () => {
    const out = applyModTemplate(current(), [mod('SD')])
    expect(out.mods.find((m) => m.modId === 'SD').explicit).toBe(true)
    expect(out.added).toEqual([])
  })

  it('replaces the list with the mod template order, keeping the scenario mods', () => {
    const out = applyModTemplate(current(), shelf(), { replace: true })
    expect(out.mods.map((m) => m.modId)).toEqual(['S', 'SD', 'X', 'A', 'Y'])
    expect(out.removed).toEqual(['A'])
  })

  it('replaces everything when no scenario is picked yet', () => {
    const noScenario = [mod('A'), mod('B')]
    const out = applyModTemplate(noScenario, shelf(), { replace: true })
    expect(out.mods.map((m) => m.modId)).toEqual(['X', 'A', 'Y'])
    expect(out.removed).toEqual(['A', 'B'])
  })

  it('leaves the caller\'s list untouched', () => {
    const before = current()
    applyModTemplate(before, shelf(), { replace: true })
    expect(before.map((m) => m.modId)).toEqual(['S', 'SD', 'A'])
    expect(before.find((m) => m.modId === 'A').version).toBe('1.0')
  })

  it('numbers newly added mods after everything already added', () => {
    const out = applyModTemplate([mod('A', { added_order: 7 })], [mod('X'), mod('Y')])
    expect(out.mods.map((m) => m.added_order)).toEqual([7, 8, 9])
  })
})

describe('adoptEditedMods (a hand-edited config.json, #171)', () => {
  // What the wizard is holding: the scenario's mod with its dependency edge and
  // a version-locked addon.
  const current = () => [
    mod('S', {
      explicit: true,
      from_scenario: true,
      dependencies: ['SD'],
      versions: ['1.0', '1.1'],
      added_order: 1,
    }),
    mod('SD', { explicit: false, added_order: 2 }),
    mod('A', { explicit: true, version: '1.0', versions: ['1.0', '2.0'], added_order: 3 }),
  ]
  // What comes back from a raw edit: config.json's flat rows, nothing else.
  const flat = [
    { modId: 'S', name: 'Scenario mod' },
    { modId: 'SD' },
    { modId: 'A', name: 'Addon', version: '1.0' },
  ]

  it('gives every row the fields the mod list walks, so nothing throws', () => {
    for (const m of adoptEditedMods(current(), [...flat, { modId: 'NEW' }])) {
      expect(Array.isArray(m.dependencies)).toBe(true)
      expect(Array.isArray(m.versions)).toBe(true)
      expect(typeof m.explicit).toBe('boolean')
    }
  })

  it('keeps the dependency graph and version history of the mods that survived', () => {
    const out = adoptEditedMods(current(), flat)
    expect(out.find((m) => m.modId === 'S')).toMatchObject({
      dependencies: ['SD'],
      versions: ['1.0', '1.1'],
      from_scenario: true,
      explicit: true,
    })
    expect(out.find((m) => m.modId === 'SD').explicit).toBe(false)
  })

  it('takes the edited name over the one the wizard had', () => {
    const out = adoptEditedMods(current(), [{ modId: 'A', name: 'Renamed by hand' }])
    expect(out[0].name).toBe('Renamed by hand')
  })

  it('adds a mod typed in by hand as an explicit pick, numbered last', () => {
    const out = adoptEditedMods(current(), [...flat, { modId: 'NEW', name: 'Typed in' }])
    expect(out.map((m) => m.modId)).toEqual(['S', 'SD', 'A', 'NEW'])
    expect(out[3]).toMatchObject({ explicit: true, added_order: 4, dependencies: [] })
  })

  it('drops a mod deleted from the JSON, and keeps the edited order', () => {
    const out = adoptEditedMods(current(), [{ modId: 'A' }, { modId: 'S' }])
    expect(out.map((m) => m.modId)).toEqual(['A', 'S'])
  })

  it('clears a version lock deleted by hand instead of inheriting it back', () => {
    const out = adoptEditedMods(current(), [{ modId: 'A', name: 'Addon' }])
    expect(out[0].version).toBe(null)
    expect(out[0].versions).toEqual(['1.0', '2.0']) // the history is still known
  })

  it('takes a version locked by hand', () => {
    const out = adoptEditedMods(current(), [{ modId: 'A', version: '2.0' }])
    expect(out[0].version).toBe('2.0')
  })

  it('survives a config with no mods at all', () => {
    expect(adoptEditedMods(current(), undefined)).toEqual([])
    expect(adoptEditedMods([], [{ modId: 'X' }])[0].modId).toBe('X')
  })
})
