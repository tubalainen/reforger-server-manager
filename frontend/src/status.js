// How an instance's state is shown (issue #76).
//
// A "running" container is not yet a server anyone can join: Reforger spends
// minutes downloading mods and loading the world before it registers with the
// backend and enters the online game state. The backend derives that from the
// server's own log (stats.server_state / summary.servers[].server_state), and
// these are the two places the difference is spelled out for the user.

const CONTAINER_BADGE = {
  running: 'text-bg-success',
  exited: 'text-bg-danger',
  created: 'text-bg-secondary',
  absent: 'text-bg-secondary',
  unknown: 'text-bg-warning',
}

export function serverStatus(status, serverState) {
  if (status === 'running' && serverState === 'starting') {
    return { label: 'starting…', long: 'Starting server…', cls: 'text-bg-warning', starting: true }
  }
  if (status === 'running' && serverState === 'online') {
    return { label: 'online', long: 'Started and online', cls: 'text-bg-success', starting: false }
  }
  // Running but the log could not be read (Docker hiccup): fall back to the
  // container's own word for it rather than claiming either way.
  return {
    label: status,
    long: status,
    cls: CONTAINER_BADGE[status] || 'text-bg-secondary',
    starting: false,
  }
}
