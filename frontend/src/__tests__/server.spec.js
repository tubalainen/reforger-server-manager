import { describe, expect, it } from 'vitest'

import { filterServers, normalizeTab, SERVER_TABS, serverSeries, tabParams } from '../server'

describe('server tabs (#189)', () => {
  it('has the five tabs in order', () => {
    expect(SERVER_TABS.map((t) => t.key)).toEqual(['overview', 'console', 'players', 'saves', 'settings'])
  })

  it('opens Overview for no tab or one that does not exist', () => {
    expect(normalizeTab(undefined)).toBe('overview')
    expect(normalizeTab('')).toBe('overview')
    expect(normalizeTab('logs')).toBe('overview') // an old or mistyped link
    expect(normalizeTab('console')).toBe('console')
  })

  it('keeps Overview on the bare server URL', () => {
    expect(tabParams(3, 'overview')).toEqual({ id: 3 })
    expect(tabParams(3, 'saves')).toEqual({ id: 3, tab: 'saves' })
  })
})

describe('serverSeries', () => {
  const history = [
    { t: 1, servers: { 1: { players: 4, cpu_percent: 10 } } },
    { t: 2, servers: {} }, // the server was stopped for this point
    { t: 3, servers: { 1: { players: 9, cpu_percent: null }, 2: { players: 1 } } },
  ]

  it("picks one server's readings, oldest first", () => {
    expect(serverSeries(history, 1, 'players')).toEqual([4, null, 9])
  })

  it('leaves a gap where the server has no reading, rather than a zero', () => {
    expect(serverSeries(history, 1, 'cpu_percent')).toEqual([10, null, null])
  })

  it('matches the id whether the route gave a string or a number', () => {
    expect(serverSeries(history, '2', 'players')).toEqual([null, null, 1])
  })

  it('copes with no history yet', () => {
    expect(serverSeries(undefined, 1, 'players')).toEqual([])
  })
})

describe('filterServers', () => {
  const servers = [
    { name: 'conflict-1', scenario_name: 'Conflict – Everon', template_name: 'Conflict Everon' },
    { name: 'gm-training', scenario_name: 'Game Master – Arland', template_name: 'GM Arland' },
    { name: 'combat-ops', scenario_name: '', template_name: 'Combat Ops Arland' },
  ]

  it('shows everything for an empty query', () => {
    expect(filterServers(servers, '  ')).toHaveLength(3)
  })

  it('matches name, scenario or template, ignoring case', () => {
    expect(filterServers(servers, 'ARLAND').map((s) => s.name)).toEqual(['gm-training', 'combat-ops'])
    expect(filterServers(servers, 'everon').map((s) => s.name)).toEqual(['conflict-1'])
    expect(filterServers(servers, 'ops').map((s) => s.name)).toEqual(['combat-ops'])
  })
})
