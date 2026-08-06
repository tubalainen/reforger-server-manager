import { describe, it, expect } from 'vitest'
import {
  ADMIN_LIMIT,
  addAdmins,
  addNotice,
  addPlayers,
  idKind,
  normalizePlayer,
  removeAdmin,
  removePlayer,
  splitIds,
} from '../players'

const STEAM = '76561198000000000'
const IDENTITY = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

describe('idKind', () => {
  it('recognises a 17-digit Steam64', () => {
    expect(idKind(STEAM)).toBe('steam')
    expect(idKind(` ${STEAM} `)).toBe('steam')
  })

  it('recognises a Bohemia identity id in either case', () => {
    expect(idKind(IDENTITY)).toBe('identity')
    expect(idKind(IDENTITY.toUpperCase())).toBe('identity')
  })

  it('rejects everything else', () => {
    // 16 and 18 digits are both wrong; a name or profile URL is not an id.
    expect(idKind('7656119800000000')).toBe(null)
    expect(idKind('765611980000000000')).toBe(null)
    expect(idKind('SomePlayer')).toBe(null)
    expect(idKind('https://steamcommunity.com/id/someone')).toBe(null)
    expect(idKind('')).toBe(null)
    expect(idKind(null)).toBe(null)
  })
})

describe('splitIds', () => {
  it('splits on newlines, commas, semicolons and spaces', () => {
    expect(splitIds(`${STEAM}, ${IDENTITY}\n 76561198111111111;x`)).toEqual([
      STEAM, IDENTITY, '76561198111111111', 'x',
    ])
  })

  it('is empty for empty input', () => {
    expect(splitIds('   ')).toEqual([])
    expect(splitIds(undefined)).toEqual([])
  })
})

describe('addAdmins', () => {
  it('adds several ids at once and reports what landed', () => {
    const r = addAdmins([], `${STEAM} ${IDENTITY}`)
    expect(r.admins).toEqual([STEAM, IDENTITY])
    expect(r.added).toHaveLength(2)
    expect(r.invalid).toEqual([])
  })

  it('reports duplicates and bad ids instead of failing the whole paste', () => {
    const r = addAdmins([STEAM], `${STEAM} nonsense 76561198111111111`)
    expect(r.admins).toEqual([STEAM, '76561198111111111'])
    expect(r.added).toEqual(['76561198111111111'])
    expect(r.duplicates).toEqual([STEAM])
    expect(r.invalid).toEqual(['nonsense'])
  })

  it('treats a differently-cased identity id as the same admin', () => {
    const r = addAdmins([IDENTITY], IDENTITY.toUpperCase())
    expect(r.admins).toEqual([IDENTITY])
    expect(r.duplicates).toHaveLength(1)
  })

  it('stops at the limit the game applies and says which were skipped', () => {
    const full = Array.from({ length: ADMIN_LIMIT }, (_, i) => String(76561198000000000 + i))
    const r = addAdmins(full, '76561198999999999')
    expect(r.admins).toHaveLength(ADMIN_LIMIT)
    expect(r.overflow).toEqual(['76561198999999999'])
    expect(addNotice(r)).toContain('at most 20')
  })
})

describe('addPlayers', () => {
  it('builds a whitelist entry from an identity id and a name', () => {
    const r = addPlayers([], IDENTITY, { name: 'Ann' })
    expect(r.players).toEqual([{ identityId: IDENTITY, name: 'Ann' }])
  })

  it('builds a ban entry with a reason when asked', () => {
    const r = addPlayers([], IDENTITY, { name: 'Bob', reason: 'Griefing', withReason: true })
    expect(r.players).toEqual([{ identityId: IDENTITY, name: 'Bob', reason: 'Griefing' }])
  })

  it('rejects a Steam id — these lists match on identity ids only', () => {
    const r = addPlayers([], STEAM, { name: 'Ann' })
    expect(r.players).toEqual([])
    expect(r.invalid).toEqual([STEAM])
    expect(addNotice(r)).toContain('not a valid id')
  })

  it('does not label a pasted batch with one player’s name', () => {
    const other = '11111111-2222-3333-4444-555555555555'
    const r = addPlayers([], `${IDENTITY} ${other}`, { name: 'Ann', reason: 'x', withReason: true })
    expect(r.players.map((p) => p.name)).toEqual(['', ''])
    expect(r.players.map((p) => p.reason)).toEqual(['', ''])
  })

  it('skips a player already on the list', () => {
    const r = addPlayers([{ identityId: IDENTITY, name: 'Ann' }], IDENTITY.toUpperCase())
    expect(r.players).toHaveLength(1)
    expect(r.duplicates).toEqual([IDENTITY.toUpperCase()])
  })
})

describe('removal helpers', () => {
  it('removes an admin regardless of case', () => {
    expect(removeAdmin([IDENTITY, STEAM], IDENTITY.toUpperCase())).toEqual([STEAM])
  })

  it('removes a player by identity id', () => {
    const list = [{ identityId: IDENTITY }, { identityId: 'x-y' }]
    expect(removePlayer(list, IDENTITY)).toEqual([{ identityId: 'x-y' }])
  })
})

describe('normalizePlayer', () => {
  it('trims and defaults the fields the backend expects', () => {
    expect(normalizePlayer({ identityId: ` ${IDENTITY} `, name: ' Ann ' })).toEqual({
      identityId: IDENTITY, name: 'Ann',
    })
    expect(normalizePlayer({ identityId: IDENTITY }, { reason: true })).toEqual({
      identityId: IDENTITY, name: '', reason: '',
    })
  })
})

describe('addNotice', () => {
  it('is empty when everything landed', () => {
    expect(addNotice({ added: [IDENTITY] })).toBe('Added 1')
    expect(addNotice({})).toBe('')
  })
})
