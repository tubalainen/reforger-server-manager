import { describe, expect, it } from 'vitest'
import { downloadView } from '../modDownload'

const MB = 1024 * 1024
const md = (over) => ({
  downloaded_bytes: 0,
  expected_bytes: 400 * MB,
  expected_state: 'ready',
  mods: 3,
  missing: 0,
  rate_bps: 0,
  growing: null,
  ...over,
})

describe('downloadView (#235)', () => {
  it('shows nothing once the server is not starting', () => {
    expect(downloadView(null).show).toBe(false)
  })

  it('waits while nothing is on disk yet', () => {
    const v = downloadView(md({}))
    expect(v.phase).toBe('waiting')
    expect(v.percent).toBeNull()
    expect(v.detail).toBe('400.0 MB to download · 3 mods with dependencies')
  })

  it('shows x of y and a percent while the folder grows', () => {
    const v = downloadView(md({ downloaded_bytes: 100 * MB, growing: true, rate_bps: 5 * MB }))
    expect(v.phase).toBe('downloading')
    expect(v.percent).toBe(25)
    expect(v.animated).toBe(true)
    expect(v.label).toBe('Downloading mods: 100.0 MB of 400.0 MB · 25 %')
    expect(v.detail).toBe('5.0 MB/s · 3 mods with dependencies')
  })

  it('counts a first reading as downloading, not done', () => {
    expect(downloadView(md({ downloaded_bytes: 10 * MB, growing: null })).phase).toBe('downloading')
  })

  it('caps the percent when the disk holds more than the Workshop said', () => {
    expect(downloadView(md({ downloaded_bytes: 500 * MB, growing: true })).percent).toBe(100)
  })

  it('falls back to bytes so far when the total is unknown', () => {
    const v = downloadView(md({
      downloaded_bytes: 2 * MB, growing: true, expected_state: 'failed', expected_bytes: null, mods: 0,
    }))
    expect(v.percent).toBeNull()
    expect(v.label).toBe('Downloading mods: 2.0 MB so far')
    expect(v.detail).toBe('the Workshop did not answer, so the total is unknown')
  })

  it('says it is loading once the folder stops growing', () => {
    const v = downloadView(md({ downloaded_bytes: 390 * MB, growing: false, missing: 1 }))
    expect(v.phase).toBe('loading')
    expect(v.percent).toBe(100)
    expect(v.animated).toBe(false)
    expect(v.detail).toBe('390.0 MB on disk · 3 mods with dependencies (1 not found on the Workshop)')
  })
})
