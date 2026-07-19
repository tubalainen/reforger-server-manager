import { describe, expect, it } from 'vitest'

import { formatBytes, formatTimestamp, formatUptime } from '../format'

describe('formatBytes', () => {
  it('has a GB tier — a baked mod folder is not "3242.5 MB"', () => {
    // The bug this file exists for: one of the four copies of this function had
    // no GB tier, so 3.4 GB of addons rendered as "3242.5 MB" (#79).
    expect(formatBytes(3.4e9)).toBe('3.17 GB')
    expect(formatBytes(2 * 1024 ** 3)).toBe('2.00 GB')
  })

  it('steps down through MB and KB', () => {
    expect(formatBytes(5 * 1024 ** 2)).toBe('5.0 MB')
    expect(formatBytes(4096)).toBe('4 KB')
  })

  it('uses decimal units when asked (Workshop download sizes)', () => {
    expect(formatBytes(1.5e9, { base: 1000 })).toBe('1.50 GB')
    expect(formatBytes(2.5e6, { base: 1000 })).toBe('2.5 MB')
  })

  it('shows the caller\'s choice for nothing-to-show', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(null, { empty: '—' })).toBe('—')
    expect(formatBytes(undefined, { empty: '' })).toBe('')
  })
})

describe('formatUptime', () => {
  it('is coarse: days+hours, hours+minutes, or minutes', () => {
    expect(formatUptime(90)).toBe('1m')
    expect(formatUptime(3600 * 5 + 60 * 7)).toBe('5h 7m')
    expect(formatUptime(86400 * 2 + 3600 * 3)).toBe('2d 3h')
  })

  it('distinguishes "not running" from "just started"', () => {
    expect(formatUptime(null)).toBe('—')
    expect(formatUptime(0)).toBe('0m')
  })
})

describe('formatTimestamp', () => {
  it('is empty for a missing timestamp rather than "Invalid Date"', () => {
    expect(formatTimestamp(null)).toBe('')
    expect(formatTimestamp(0)).toBe('')
    expect(formatTimestamp(1768000000)).not.toBe('')
  })
})

