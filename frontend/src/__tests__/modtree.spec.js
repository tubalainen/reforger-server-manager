import { describe, expect, it } from 'vitest'

import { allModIds, buildForest } from '../modtree'

const mods = [
  {
    mod_id: 'AAAA', name: 'Alpha', persist: true, orphaned: false,
    templates: [{ id: 1, name: 'T1' }],
    instances: [{ id: 9, name: 'srv', template: 'T1', version: '1.0' }],
  },
  { mod_id: 'BBBB', name: 'Bravo', persist: false, orphaned: true, templates: [], instances: [] },
]

describe('buildForest', () => {
  it('makes every registered mod a root', () => {
    const forest = buildForest(mods, { edges: {}, names: {} })
    expect(forest.map((n) => n.modId)).toEqual(['AAAA', 'BBBB'])
    expect(forest.every((n) => n.registered)).toBe(true)
  })

  it('nests resolved dependencies, carrying names for unregistered deps', () => {
    const tree = { edges: { AAAA: ['CCCC'] }, names: { CCCC: 'Core' } }
    const [alpha] = buildForest(mods, tree)
    expect(alpha.children).toHaveLength(1)
    const core = alpha.children[0]
    expect(core.modId).toBe('CCCC')
    expect(core.name).toBe('Core')
    expect(core.registered).toBe(false) // only a dependency, never added on its own
  })

  it('shows a standalone-and-dependency mod both as a root and nested', () => {
    // Bravo is registered (a root) AND a dependency of Alpha.
    const tree = { edges: { AAAA: ['BBBB'] }, names: {} }
    const forest = buildForest(mods, tree)
    const roots = forest.map((n) => n.modId)
    expect(roots).toContain('BBBB') // still its own root
    const nested = forest[0].children[0]
    expect(nested.modId).toBe('BBBB')
    expect(nested.registered).toBe(true) // resolved back to its registry entry
    expect(nested.persist).toBe(false)
  })

  it('passes persist / orphaned / instance metadata through to nodes', () => {
    const [alpha] = buildForest(mods, { edges: {}, names: {} })
    expect(alpha.persist).toBe(true)
    expect(alpha.instances[0].version).toBe('1.0')
    const bravo = buildForest(mods, {})[1]
    expect(bravo.orphaned).toBe(true)
  })

  it('guards against dependency cycles', () => {
    const tree = { edges: { AAAA: ['BBBB'], BBBB: ['AAAA'] }, names: {} }
    const [alpha] = buildForest(mods, tree)
    // AAAA -> BBBB -> AAAA stops here rather than recursing forever.
    expect(alpha.children[0].modId).toBe('BBBB')
    expect(alpha.children[0].children[0].modId).toBe('AAAA')
    expect(alpha.children[0].children[0].children).toEqual([])
  })

  it('tolerates an empty/missing tree', () => {
    expect(buildForest(mods, null).every((n) => n.children.length === 0)).toBe(true)
    expect(buildForest([], null)).toEqual([])
  })
})

describe('allModIds', () => {
  it('collects every id in the forest, deduplicated', () => {
    const tree = { edges: { AAAA: ['BBBB', 'CCCC'] }, names: {} }
    const forest = buildForest(mods, tree)
    expect(allModIds(forest).sort()).toEqual(['AAAA', 'BBBB', 'CCCC'])
  })
})
