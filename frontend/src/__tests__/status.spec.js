import { describe, expect, it } from 'vitest'

import { serverStatus } from '../status'

describe('serverStatus', () => {
  it('does not call a loading server "online" (#76)', () => {
    // A running CONTAINER is not a joinable server: mods and the world load first.
    const s = serverStatus('running', 'starting')
    expect(s.label).toBe('starting…')
    expect(s.long).toBe('Starting server…')
    expect(s.cls).toBe('text-bg-warning')
    expect(s.starting).toBe(true)
  })

  it('goes green only once the server says it is up', () => {
    const s = serverStatus('running', 'online')
    expect(s.label).toBe('online')
    expect(s.long).toBe('Started and online')
    expect(s.cls).toBe('text-bg-success')
    expect(s.starting).toBe(false)
  })

  it('shows the pending action the moment the user asks for it (#76)', () => {
    // The old server stays genuinely "online" until it exits — Reforger can take
    // tens of seconds to honour SIGTERM — so without this the badge sits on
    // "Started and online" after a Restart and the button looks broken.
    const s = serverStatus('running', 'online', 'restart')
    expect(s.label).toBe('restarting…')
    expect(s.note).toBe('shutting the old server down')
    expect(s.starting).toBe(true)
  })

  it('falls back to the container status when the log could not be read', () => {
    // Don't claim either way on a Docker hiccup.
    expect(serverStatus('running', null).label).toBe('running')
    expect(serverStatus('exited', null).cls).toBe('text-bg-danger')
    expect(serverStatus('absent', null).cls).toBe('text-bg-secondary')
    expect(serverStatus('weird-new-state', null).cls).toBe('text-bg-secondary')
  })
})
