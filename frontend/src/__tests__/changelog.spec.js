import { describe, expect, it } from 'vitest'
import { categoryBadge, filterEntries, groupEntries } from '../changelog'

const e = (id, at, category, summary) => ({ id, changed_at: at, display: `@${at}`, category, summary })

describe('groupEntries (#112, #223)', () => {
  it('groups one save under its timestamp and lifts the comment out', () => {
    const groups = groupEntries([
      e(3, 't2', 'setting', 'game.maxPlayers: 32 → 64'),
      e(4, 't2', 'note', 'more slots for Friday'),
      e(1, 't1', 'meta', 'Template created'),
    ])
    expect(groups).toHaveLength(2)
    expect(groups[0].display).toBe('@t2')
    expect(groups[0].notes).toEqual(['more slots for Friday'])
    expect(groups[0].items.map((i) => i.id)).toEqual([3])
    expect(groups[1].notes).toEqual([])
  })

  it('keeps a group that holds only a comment', () => {
    const groups = groupEntries([e(1, 't1', 'note', 'just a note')])
    expect(groups[0].items).toEqual([])
    expect(groups[0].notes).toEqual(['just a note'])
  })
})

describe('filterEntries', () => {
  it('matches lines and comments alike, case-insensitively', () => {
    const rows = [e(1, 't', 'note', 'Friday op'), e(2, 't', 'mod', 'Added mod RHS')]
    expect(filterEntries(rows, 'friday').map((r) => r.id)).toEqual([1])
    expect(filterEntries(rows, '  ')).toBe(rows)
  })
})

describe('categoryBadge', () => {
  it('names a stage move and falls back for the unknown', () => {
    expect(categoryBadge('release').label).toBe('stage')
    expect(categoryBadge('other')).toEqual({ label: 'other', cls: 'text-bg-secondary' })
  })
})
