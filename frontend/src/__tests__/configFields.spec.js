import { describe, expect, it } from 'vitest'
import {
  advancedGroups,
  changedCount,
  elsewhereMatches,
  isChanged,
  limitsFor,
  linesToList,
  listToLines,
  matchesField,
} from '../configFields'

const catalog = {
  groups: [
    { id: 'game_properties', title: 'Game properties', path: 'game.gameProperties' },
    { id: 'von', title: 'Voice (VON)', path: 'game.gameProperties' },
    { id: 'rcon', title: 'RCON', path: 'rcon' },
  ],
  fields: [
    {
      name: 'battleye', group: 'game_properties', label: 'BattlEye', key: 'battlEye',
      path: 'game.gameProperties.battlEye', help: 'Anti-cheat.', kind: 'bool', default: true,
      card: 'server',
    },
    {
      name: 'server_min_grass_distance', group: 'game_properties', label: 'Min grass distance',
      key: 'serverMinGrassDistance', path: 'game.gameProperties.serverMinGrassDistance',
      help: 'Grass.', kind: 'int', default: 50, min: 50, max: 150, card: 'advanced',
    },
    {
      name: 'von_can_transmit_cross_faction', group: 'von', label: 'Cross-faction VON',
      key: 'VONCanTransmitCrossFaction', path: 'game.gameProperties.VONCanTransmitCrossFaction',
      help: "Lets players talk on other factions' radio channels.", kind: 'bool', default: false,
      card: 'advanced',
    },
    {
      name: 'rcon_password', group: 'rcon', label: 'RCON password', key: 'password',
      path: 'rcon.password', help: 'Turns RCON on.', kind: 'text', default: '', card: 'server',
    },
  ],
}
const field = (name) => catalog.fields.find((f) => f.name === name)

describe('matchesField', () => {
  it('matches the config key, the label and the help text, ignoring case', () => {
    const von = field('von_can_transmit_cross_faction')
    expect(matchesField(von, 'VONCanTransmit')).toBe(true)
    expect(matchesField(von, 'cross-faction')).toBe(true)
    expect(matchesField(von, 'RADIO')).toBe(true)
    expect(matchesField(von, 'grass')).toBe(false)
  })

  it('matches everything on an empty or blank query', () => {
    expect(matchesField(field('battleye'), '')).toBe(true)
    expect(matchesField(field('battleye'), '   ')).toBe(true)
  })
})

describe('advancedGroups', () => {
  it('lists only Advanced settings, in catalog group order', () => {
    const groups = advancedGroups(catalog, '')
    expect(groups.map((g) => g.id)).toEqual(['game_properties', 'von'])
    expect(groups[0].fields.map((f) => f.name)).toEqual(['server_min_grass_distance'])
  })

  it('drops groups with no match', () => {
    expect(advancedGroups(catalog, 'VON').map((g) => g.id)).toEqual(['von'])
    expect(advancedGroups(catalog, 'nothing like this')).toEqual([])
  })

  it('is empty before the catalog has loaded', () => {
    expect(advancedGroups(null, '')).toEqual([])
  })
})

describe('elsewhereMatches', () => {
  it('finds settings on another card only when something is typed', () => {
    expect(elsewhereMatches(catalog, '')).toEqual([])
    expect(elsewhereMatches(catalog, 'battleye').map((f) => f.name)).toEqual(['battleye'])
  })
})

describe('isChanged / changedCount', () => {
  it('compares against the default', () => {
    expect(isChanged(field('server_min_grass_distance'), 50)).toBe(false)
    expect(isChanged(field('server_min_grass_distance'), 100)).toBe(true)
  })

  it('treats a blank text field as the empty default', () => {
    expect(isChanged(field('rcon_password'), undefined)).toBe(false)
    expect(isChanged(field('rcon_password'), 'secret')).toBe(true)
  })

  it('counts only Advanced settings', () => {
    const spec = { battleye: false, server_min_grass_distance: 50, von_can_transmit_cross_faction: true }
    expect(changedCount(catalog, spec)).toBe(1)
    expect(changedCount(null, spec)).toBe(0)
  })
})

describe('list settings', () => {
  const list = { name: 'rcon_whitelist', kind: 'list', default: [] }

  it('reads one entry per line: trimmed, blanks dropped, each once', () => {
    expect(linesToList(' #kick \n\n#players\n#kick\n')).toEqual(['#kick', '#players'])
    expect(linesToList('')).toEqual([])
    expect(listToLines(['#kick', '#players'])).toBe('#kick\n#players')
    expect(listToLines(undefined)).toBe('')
    // not a list at all: nothing to show rather than a throw
    expect(listToLines(120)).toBe('')
    expect(listToLines(true)).toBe('')
  })

  it('is changed when it holds anything', () => {
    expect(isChanged(list, [])).toBe(false)
    expect(isChanged(list, undefined)).toBe(false)
    expect(isChanged(list, ['#kick'])).toBe(true)
  })
})

describe('limitsFor', () => {
  it('returns the min and max the model allows', () => {
    expect(limitsFor(catalog, 'server_min_grass_distance')).toEqual({ min: 50, max: 150 })
    expect(limitsFor(catalog, 'battleye')).toEqual({})
    expect(limitsFor(null, 'server_min_grass_distance')).toEqual({})
  })
})
