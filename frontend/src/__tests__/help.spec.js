import { describe, expect, it } from 'vitest'

import { placePopover } from '../help'

const viewport = { width: 1000, height: 800 }
const size = { width: 300, height: 120 }
const anchor = (left, top) => ({ left, right: left + 18, top, bottom: top + 18 })

describe('placePopover', () => {
  it('opens under the "?" lined up with it', () => {
    expect(placePopover(anchor(100, 50), size, viewport)).toEqual({ left: 100, top: 74 })
  })

  it('slides left rather than running off the right edge', () => {
    // A "?" near the right edge of the screen, as at the end of a card header.
    expect(placePopover(anchor(950, 50), size, viewport).left).toBe(1000 - 300 - 8)
  })

  it('never starts left of the screen', () => {
    const narrow = { width: 320, height: 800 }
    expect(placePopover(anchor(2, 50), size, narrow).left).toBe(8)
  })

  it('opens above the "?" when there is no room below', () => {
    expect(placePopover(anchor(100, 740), size, viewport).top).toBe(740 - 6 - 120)
  })

  it('stays below when there is no room above either', () => {
    const short = { width: 1000, height: 200 }
    expect(placePopover(anchor(100, 60), size, short).top).toBe(84)
  })
})
