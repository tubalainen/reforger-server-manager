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

describe('downloadView with the run plan (#246)', () => {
  const plan = (over) => md({
    downloaded_bytes: 20 * 1024 * MB, // the whole template already on disk
    growing: true,
    rate_bps: 10 * MB,
    plan_mods: 2,
    plan_done: 0,
    plan_bytes: 300 * MB,
    plan_done_bytes: 0,
    plan_kind: 'update',
    plan_names: ['IwoJima2026', 'CongosDynamicLights'],
    ...over,
  })

  it('counts only the mods being updated, not everything on disk', () => {
    const v = downloadView(plan({ plan_done: 1, plan_done_bytes: 100 * MB }))
    expect(v.phase).toBe('downloading')
    expect(v.percent).toBe(33)
    expect(v.label).toBe('Updating 2 mods: 1 of 2 done')
    expect(v.detail).toBe('IwoJima2026, CongosDynamicLights · 10.0 MB/s')
  })

  it('says the mods are up to date when nothing needs fetching', () => {
    const v = downloadView(plan({ plan_mods: 0, plan_bytes: 0 }))
    expect(v.phase).toBe('loading')
    expect(v.label).toBe('Mods up to date — loading the world…')
  })

  it('moves on to loading once every mod is done and the disk is quiet', () => {
    const v = downloadView(plan({ plan_done: 2, plan_done_bytes: 300 * MB, growing: false }))
    expect(v.label).toBe('Mods updated — loading the world…')
  })

  it('shows bytes for a first download', () => {
    const v = downloadView(plan({ plan_kind: 'download', plan_done_bytes: 150 * MB, plan_names: [] }))
    expect(v.label).toBe('Downloading mods: 150.0 MB of 300.0 MB · 50 %')
    expect(v.detail).toBe('10.0 MB/s · 0 of 2 mods done')
  })
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
