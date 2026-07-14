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

// While a stop/start/restart the user asked for is still in flight, say so. The
// old server stays genuinely "online" until it exits — Reforger can take tens of
// seconds to honour SIGTERM — so without this the badge sits on "Started and
// online" after a Restart and the button looks broken (#76).
const PENDING = {
  restart: { label: 'restarting…', long: 'Restarting…', note: 'shutting the old server down' },
  stop: { label: 'stopping…', long: 'Stopping…', note: '' },
  start: { label: 'starting…', long: 'Starting server…', note: '' },
}

export function serverStatus(status, serverState, pending) {
  if (pending && PENDING[pending]) {
    return { ...PENDING[pending], cls: 'text-bg-warning', starting: true }
  }
  if (status === 'running' && serverState === 'starting') {
    return {
      label: 'starting…',
      long: 'Starting server…',
      note: 'loading mods & world',
      cls: 'text-bg-warning',
      starting: true,
    }
  }
  if (status === 'running' && serverState === 'online') {
    return {
      label: 'online',
      long: 'Started and online',
      note: '',
      cls: 'text-bg-success',
      starting: false,
    }
  }
  // Running but the log could not be read (Docker hiccup): fall back to the
  // container's own word for it rather than claiming either way.
  return {
    label: status,
    long: status,
    note: '',
    cls: CONTAINER_BADGE[status] || 'text-bg-secondary',
    starting: false,
  }
}
