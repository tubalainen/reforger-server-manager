import { describe, expect, it } from 'vitest'

import { modStateLabel, modsSummary } from '../modVersions'

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
