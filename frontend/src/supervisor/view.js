// What the Server Supervisor's page derives from /api/overview (#204, v0.67.0).
// Kept free of Vue so it can be tested on its own.

import { formatBytes, formatUptime } from '../format'
import { serverStatus } from '../status'

export const SEVERITY_DOT = { danger: 'bg-danger', warning: 'bg-warning', info: 'bg-info' }

const isRunning = (s) => s.status === 'running'

// How a container ends when it is stopped on purpose: on its own, or by Docker's
// SIGTERM (143) or the SIGKILL after the grace period (137).
const CLEAN_EXIT = new Set([0, 137, 143])

/** A server's status as the manager's own Servers page would show it. */
export function serverRow(s) {
  let st = serverStatus(s.status, s.server_state)
  if (s.status === 'exited') {
    st = CLEAN_EXIT.has(s.exit_code)
      ? { ...st, label: 'stopped', long: 'Stopped', cls: 'text-bg-secondary' }
      : { ...st, label: `crashed (${s.exit_code})`, long: `Exited with code ${s.exit_code}` }
  }
  const max = s.max_players != null ? ` / ${s.max_players}` : ''
  return {
    status: st,
    dot: st.cls.replace('text-bg-', 'bg-'),
    players: `${isRunning(s) && s.players != null ? s.players : '—'}${max}`,
    fill:
      isRunning(s) && s.players != null && s.max_players
        ? Math.min(100, Math.round((s.players / s.max_players) * 100))
        : 0,
    fps: isRunning(s) && s.server_fps != null ? Math.round(s.server_fps) : '—',
    cpu: isRunning(s) && s.cpu_percent != null ? `${s.cpu_percent}%` : '—',
    mem: isRunning(s) ? formatBytes(s.mem_bytes, { empty: '—' }) : '—',
    uptime: isRunning(s) ? formatUptime(s.uptime_seconds) : '—',
    ports: [s.ports?.game, s.ports?.a2s].filter((p) => p != null).join(' · ') || '—',
  }
}

/**
 * One line about a stack's manager and gate, and the colour of its dot.
 * The worst of the two wins: a manager without a working gate cannot run servers.
 */
export function stackHealth(stack) {
  const m = stack.manager
  const g = stack.gate
  if (!m) return { dot: 'bg-secondary', text: 'No manager container found' }
  if (!m.running) return { dot: 'bg-danger', text: `Manager ${m.status}` }
  if (!g) return { dot: 'bg-danger', text: 'Manager up · no Docker gate' }
  if (!g.running) return { dot: 'bg-danger', text: `Manager up · Docker gate ${g.status}` }
  const version = m.version ? `v${m.version}` : 'before v0.67.0'
  return { dot: 'bg-success', text: `Manager ${version} · up ${formatUptime(m.uptime_seconds)}` }
}

/** The ports a stack takes, as short labelled pairs. */
export function stackPorts(stack) {
  const p = stack.ports
  if (!p) return []
  return [
    ['GUI', p.web, 'TCP'],
    ['Game', p.game, 'UDP'],
    ['A2S', p.a2s, 'UDP'],
    ['RCON', p.rcon, 'UDP'],
  ]
    .filter(([, value]) => value)
    .map(([label, value, proto]) => ({ label, value, proto }))
}

/** A stack's own figures, one line. */
export function stackSummary(stack) {
  const t = stack.totals
  if (!t.servers) return 'No game servers'
  const parts = [
    `${t.running} of ${t.servers} running`,
    `${t.players} player${t.players === 1 ? '' : 's'}`,
  ]
  if (t.cpu_percent != null) parts.push(`CPU ${t.cpu_percent}%`)
  if (t.mem_bytes != null) parts.push(formatBytes(t.mem_bytes))
  return parts.join(' · ')
}

/** The totals strip, each figure with its history for a sparkline. */
export function totalTiles(overview) {
  if (!overview) return []
  const t = overview.totals
  const host = overview.host || {}
  const series = (key) => (overview.history || []).map((p) => p[key])
  return [
    {
      key: 'stacks',
      label: 'Stacks',
      value: t.stacks,
      unit: t.stacks ? ` · ${t.managers_up} up` : '',
      values: [],
    },
    {
      key: 'running',
      label: 'Servers running',
      value: t.running,
      unit: ` / ${t.servers}`,
      values: series('running'),
    },
    {
      key: 'players',
      label: 'Players',
      value: t.players,
      unit: t.max_players ? ` / ${t.max_players}` : '',
      values: series('players'),
    },
    {
      key: 'cpu',
      label: host.ncpu ? `CPU · ${host.ncpu} cores` : 'CPU',
      value: t.cpu_percent != null ? t.cpu_percent : '—',
      unit: t.cpu_percent != null ? '%' : '',
      values: series('cpu_percent'),
      title: "Every game server's share of this whole machine, added up",
    },
    {
      key: 'mem',
      label: 'Memory',
      value: formatBytes(t.mem_bytes, { empty: '—' }),
      unit: host.mem_total ? ` / ${formatBytes(host.mem_total)}` : '',
      values: series('mem_bytes'),
      title: 'Used by the game servers, of all the memory this machine has',
    },
  ]
}

/** The machine in one line: its OS and Docker. */
export function hostLine(host) {
  if (!host) return ''
  return [host.os, host.docker_version && `Docker ${host.docker_version}`]
    .filter(Boolean)
    .join(' · ')
}

/**
 * Where a team's GUI would be, seen from this browser: the same host name as the
 * Supervisor, on the stack's GUI port. Only a guess — a GUI bound to 127.0.0.1 is
 * not reachable from another machine — so the page offers it as a plain link.
 */
export function guiUrl(stack, location = globalThis.location) {
  const port = stack.ports?.web
  if (!port || !location?.hostname) return null
  const host = location.hostname.includes(':') ? `[${location.hostname}]` : location.hostname
  return `${location.protocol}//${host}:${port}`
}
