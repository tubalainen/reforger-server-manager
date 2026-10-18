import { describe, expect, it } from 'vitest'
import {
  defaultReleaseTarget,
  groupByStage,
  isLocked,
  promoteAction,
  releaseSummary,
  staleCopyWarning,
  stageInfo,
  versionLabel,
  versionOptions,
  versionsText,
} from '../stages'

const prod = (id, name, prod_version) => ({ id, name, stage: 'prod', prod_version })

describe('groupByStage (#223)', () => {
  it('groups Prod, Dev, Test in that order and skips empty groups', () => {
    const items = [
      { id: 1, stage: 'test' },
      { id: 2, stage: 'prod' },
      { id: 3, stage: 'test' },
    ]
    const groups = groupByStage(items)
    expect(groups.map((g) => g.stage)).toEqual(['prod', 'test'])
    expect(groups[1].items.map((i) => i.id)).toEqual([1, 3]) // order kept
    expect(groups[0].label).toBe('Prod')
  })

  it('files an unknown stage under Test', () => {
    expect(groupByStage([{ id: 1, stage: 'weird' }])[0].stage).toBe('test')
  })
})

describe('stage helpers', () => {
  it('offers one move up, none from Prod', () => {
    expect(promoteAction('test')).toEqual({ label: 'To Dev', release: false })
    expect(promoteAction('dev')).toEqual({ label: 'Release…', release: true })
    expect(promoteAction('prod')).toBeNull()
  })

  it('only Prod is locked and versioned', () => {
    expect(isLocked({ stage: 'prod' })).toBe(true)
    expect(isLocked({ stage: 'dev' })).toBe(false)
    expect(versionLabel(prod(1, 'A', 3))).toBe('v3')
    expect(versionLabel({ stage: 'dev', prod_version: 3 })).toBe('')
    expect(stageInfo('nope').label).toBe('Test')
    expect(versionsText(1)).toBe('its released version')
    expect(versionsText(3)).toBe('all 3 of its released versions')
  })
})

describe('defaultReleaseTarget', () => {
  const prods = [prod(7, 'Conflict', 2)]
  it('is the Prod item a copy came from', () => {
    expect(defaultReleaseTarget({ based_on: { id: 7, version: 2 } }, prods)).toBe(7)
  })
  it('is a new Prod item when there is no origin, or it is gone', () => {
    expect(defaultReleaseTarget({ based_on: null }, prods)).toBeNull()
    expect(defaultReleaseTarget({ based_on: { id: 9 } }, prods)).toBeNull()
  })
})

describe('staleCopyWarning', () => {
  const target = prod(7, 'Conflict', 4)
  it('warns when the copy is older than the target', () => {
    expect(staleCopyWarning({ based_on: { id: 7, version: 2 } }, target)).toMatch(/copied from v2.*v4/)
  })
  it('stays quiet for an up-to-date copy or another target', () => {
    expect(staleCopyWarning({ based_on: { id: 7, version: 4 } }, target)).toBe('')
    expect(staleCopyWarning({ based_on: { id: 8, version: 1 } }, target)).toBe('')
    expect(staleCopyWarning({ based_on: null }, target)).toBe('')
    expect(staleCopyWarning({ based_on: { id: 7, version: 1 } }, null)).toBe('')
  })
})

describe('releaseSummary', () => {
  it('says what a release does', () => {
    const item = { name: 'Copy of Conflict' }
    expect(releaseSummary(item, null, 'mod list')).toBe("'Copy of Conflict' becomes a new Prod mod list at v1.")
    expect(releaseSummary(item, prod(7, 'Conflict', 4))).toMatch(/'Conflict' gets v5/)
  })
})

describe('versionOptions', () => {
  it('offers latest first, then every release newest first', () => {
    const t = {
      stage: 'prod',
      prod_version: 2,
      versions: [
        { version: 2, released: '2026-10-18', note: 'more slots' },
        { version: 1, released: '2026-10-01', note: '' },
      ],
    }
    expect(versionOptions(t)).toEqual([
      { value: null, label: 'Latest (v2)' },
      { value: 2, label: 'v2 · 2026-10-18 · more slots' },
      { value: 1, label: 'v1 · 2026-10-01' },
    ])
  })

  it('offers nothing outside Prod', () => {
    expect(versionOptions({ stage: 'dev', versions: [] })).toEqual([])
    expect(versionOptions(undefined)).toEqual([])
  })
})
