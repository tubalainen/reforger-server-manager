import { describe, expect, it } from 'vitest'

import { isErrorLine } from '../log'

// Real-shaped Reforger log lines (#108): severity is tagged per line as
// "COMPONENT (E): message"; FATAL/ERROR appear outside that scheme too.
describe('isErrorLine', () => {
  it('flags engine (E) and (F) tagged lines', () => {
    expect(isErrorLine('17:29:14.322  BACKEND (E): Curl error=Could not resolve hostname')).toBe(true)
    expect(isErrorLine('17:29:14.322  SCRIPT       (E): NULL pointer dereference')).toBe(true)
    expect(isErrorLine('17:29:14.322  ENGINE  (F): Out of memory')).toBe(true)
  })

  it('flags FATAL and bare ERROR lines', () => {
    expect(isErrorLine('FATAL: Cannot load world')).toBe(true)
    expect(isErrorLine('SteamAPI init ERROR')).toBe(true)
  })

  it('leaves warnings, info and stats lines alone', () => {
    expect(isErrorLine('17:29:14.322  NETWORK (W): Connectivity issues detected')).toBe(false)
    expect(isErrorLine('17:29:14.322  DEFAULT      : Loading world Everon')).toBe(false)
    expect(isErrorLine('FPS: 59.9, Mem: 2400000 kB, Player: 12')).toBe(false)
    expect(isErrorLine('Server registered with address: 1.2.3.4:2001')).toBe(false)
  })

  it('does not fire on words that merely contain the letters', () => {
    expect(isErrorLine('ErrorHandler initialized')).toBe(false)
    expect(isErrorLine('')).toBe(false)
    expect(isErrorLine(null)).toBe(false)
  })
})
