import { describe, expect, it } from 'vitest'

import {
  guiUrl,
  hostLine,
  serverRow,
  stackHealth,
  stackPorts,
  stackSummary,
  totalTiles,
} from '../supervisor/view'

const server = (over = {}) => ({
  status: 'running',
  server_state: 'online',
  players: 12,
  max_players: 64,
  server_fps: 59.6,
  cpu_percent: 20.5,
  mem_bytes: 3 * 2 ** 30,
  uptime_seconds: 7200,
  ports: { game: 2001, a2s: 17777, rcon: 19999 },
  ...over,
})

describe('serverRow', () => {
  it('reads like the Servers page of the manager', () => {
    const row = serverRow(server())
    expect(row.status.label).toBe('online')
    expect(row.dot).toBe('bg-success')
    expect(row.players).toBe('12 / 64')
    expect(row.fill).toBe(19)
    expect(row.fps).toBe(60)
    expect(row.cpu).toBe('20.5%')
    expect(row.mem).toBe('3.00 GB')
    expect(row.uptime).toBe('2h 0m')
    expect(row.ports).toBe('2001 · 17777')
  })

  it('tells a stopped server from a crashed one', () => {
    const stopped = serverRow(server({ status: 'exited', exit_code: 143 }))
    expect([stopped.status.label, stopped.dot]).toEqual(['stopped', 'bg-secondary'])
    const crashed = serverRow(server({ status: 'exited', exit_code: 139 }))
    expect([crashed.status.label, crashed.dot]).toEqual(['crashed (139)', 'bg-danger'])
  })

  it('shows no live figures for a stopped server', () => {
    const row = serverRow(server({ status: 'exited', server_state: null, exit_code: 0 }))
    expect(row.players).toBe('— / 64')
    expect(row.fill).toBe(0)
    expect(row.cpu).toBe('—')
    expect(row.uptime).toBe('—')
  })

  it('leaves out a player limit it does not know (a server from before v0.67.0)', () => {
    expect(serverRow(server({ max_players: null })).players).toBe('12')
  })
})

describe('stackHealth', () => {
  const up = { running: true, status: 'running', version: '0.67.0', uptime_seconds: 90000 }
  it('is green only with a manager and a gate running', () => {
    expect(stackHealth({ manager: up, gate: { running: true } })).toEqual({
      dot: 'bg-success',
      text: 'Manager v0.67.0 · up 1d 1h',
    })
  })
  it('is red without a gate, or with either down', () => {
    expect(stackHealth({ manager: up, gate: null }).dot).toBe('bg-danger')
    expect(stackHealth({ manager: up, gate: { running: false, status: 'exited' } }).text).toBe(
      'Manager up · Docker gate exited',
    )
    expect(stackHealth({ manager: { running: false, status: 'exited' }, gate: null }).text).toBe(
      'Manager exited',
    )
  })
  it('says when a manager is too old to report its version', () => {
    expect(stackHealth({ manager: { ...up, version: null }, gate: { running: true } }).text).toMatch(
      /before v0\.67\.0/,
    )
  })
})

describe('stack lines', () => {
  it('lists the ports a stack takes', () => {
    const ports = stackPorts({ ports: { web: '7780', game: '2001-2020', a2s: null, rcon: '19999-20018' } })
    expect(ports.map((p) => p.label)).toEqual(['GUI', 'Game', 'RCON'])
    expect(stackPorts({ ports: null })).toEqual([])
  })

  it('sums a stack up in one line', () => {
    const totals = { servers: 3, running: 2, players: 1, cpu_percent: 12.5, mem_bytes: 2 ** 30 }
    expect(stackSummary({ totals })).toBe('2 of 3 running · 1 player · CPU 12.5% · 1.00 GB')
    expect(stackSummary({ totals: { servers: 0 } })).toBe('No game servers')
  })
})

describe('totalTiles', () => {
  it('draws each figure with its last hour', () => {
    const tiles = totalTiles({
      totals: { stacks: 2, managers_up: 2, servers: 3, running: 2, players: 7, max_players: 80,
        cpu_percent: 31.5, mem_bytes: 2 ** 31 },
      host: { ncpu: 8, mem_total: 2 ** 34 },
      history: [{ running: 1, players: 3 }, { running: 2, players: 7 }],
    })
    const byKey = Object.fromEntries(tiles.map((t) => [t.key, t]))
    expect(byKey.players.unit).toBe(' / 80')
    expect(byKey.players.values).toEqual([3, 7])
    expect(byKey.cpu.label).toBe('CPU · 8 cores')
    expect(byKey.mem.unit).toBe(' / 16.00 GB')
  })
})

describe('hostLine and guiUrl', () => {
  it('names the machine', () => {
    expect(hostLine({ os: 'Ubuntu 24.04', docker_version: '28.1.1' })).toBe('Ubuntu 24.04 · Docker 28.1.1')
    expect(hostLine(null)).toBe('')
  })

  it('points at a team GUI on the same host name', () => {
    const loc = { protocol: 'http:', hostname: '192.168.1.20' }
    expect(guiUrl({ ports: { web: '7781' } }, loc)).toBe('http://192.168.1.20:7781')
    expect(guiUrl({ ports: { web: '7781' } }, { protocol: 'http:', hostname: '::1' })).toBe('http://[::1]:7781')
    expect(guiUrl({ ports: null }, loc)).toBeNull()
  })
})
