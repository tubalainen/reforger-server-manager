import { describe, expect, it } from 'vitest'

import { loadOrderSummary, modStateLabel, modsSummary } from '../modVersions'

describe('loadOrderSummary (#257)', () => {
  const mounted = (n, unlisted = 0) => [
    ...Array.from({ length: n }, (_, i) => ({ base_game: false, listed_at: i < n - unlisted ? i + 1 : null })),
    { base_game: true },
    { base_game: true },
  ]
  it('says how the engine order relates to the list', () => {
    expect(loadOrderSummary({ available: true, relation: 'reversed', pairs: 3, inverted: 3, unlisted: 0, mounted: mounted(3) }))
      .toBe('The engine loaded 3 mods in the reverse of the order of your list, then the base game.')
    expect(loadOrderSummary({ available: true, relation: 'reordered', pairs: 45, inverted: 43, unlisted: 1, mounted: mounted(11, 1) }))
      .toBe('The engine loaded 11 mods mostly in reverse of your list (43 of 45 pairs swapped), then the base game.' +
        ' 1 of them is not in your list: a dependency the server fetched itself.')
    expect(loadOrderSummary({ available: true, relation: 'same', pairs: 1, inverted: 0, unlisted: 0, mounted: mounted(2) }))
      .toContain('in the order of your list')
  })
  it('says nothing without a log', () => {
    expect(loadOrderSummary({ available: false })).toBe('')
  })
})

describe('modsSummary', () => {
  it('counts each state, leaving out the empty ones', () => {
    const mods = [
      { state: 'current' },
      { state: 'current' },
      { state: 'behind' },
      { state: 'locked' },
    ]
    expect(modsSummary({ mods })).toBe('4 mods: 2 up to date, 1 with an update, 1 locked.')
  })

  it('says nothing for a template without mods', () => {
    expect(modsSummary({ mods: [] })).toBe('')
  })

  it('uses the singular for one mod', () => {
    expect(modsSummary({ mods: [{ state: 'missing' }] })).toBe('1 mod: 1 not downloaded yet.')
  })
})

describe('modStateLabel', () => {
  it('names every state the backend reports', () => {
    for (const s of ['behind', 'current', 'locked', 'missing', 'unreadable', 'unknown']) {
      expect(modStateLabel(s)).not.toBe(s)
    }
  })

  it('names the version a locked mod is held at', () => {
    expect(modStateLabel('locked', '0.12.3')).toBe('Locked at 0.12.3')
  })
})
