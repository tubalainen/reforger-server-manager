import { describe, expect, it } from 'vitest'

import {
  changedFields,
  describeSteps,
  mergeIncoming,
  normaliseTimes,
  planSave,
  settingsFrom,
  validate,
} from '../settingsForm'

const inst = (over = {}) => ({
  id: 3,
  name: 'conflict-1',
  branch: 'stable',
  template_id: 1,
  game_port: 2001,
  a2s_port: 17777,
  rcon_port: 19999,
  auto_restart: true,
  auto_start: true,
  restart_times: ['20:00', '04:00'],
  status: 'exited',
  ...over,
})

describe('normaliseTimes', () => {
  it('matches what the backend stores: padded, sorted, no repeats', () => {
    expect(normaliseTimes(['20:00', '4:00', '04:00', ' 12:30 '])).toEqual(['04:00', '12:30', '20:00'])
  })

  it('drops what is not a time', () => {
    expect(normaliseTimes(['25:00', 'noon', '', null])).toEqual([])
  })
})

describe('changedFields', () => {
  it('sees nothing when the form holds what the server has', () => {
    const base = settingsFrom(inst())
    expect(changedFields(base, { ...base })).toEqual([])
  })

  it('compares the restart times by value, not by array identity', () => {
    const base = settingsFrom(inst())
    expect(changedFields(base, { ...base, restart_times: ['04:00', '20:00'] })).toEqual([])
    expect(changedFields(base, { ...base, restart_times: ['04:00'] })).toEqual(['restart_times'])
  })
})

describe('mergeIncoming', () => {
  it('lets untouched fields follow the server', () => {
    const base = settingsFrom(inst())
    const { draft } = mergeIncoming(base, { ...base }, inst({ auto_start: false }))
    expect(draft.auto_start).toBe(false)
  })

  it("keeps what the user typed while a poll comes in", () => {
    const base = settingsFrom(inst())
    const edited = { ...base, name: 'conflict-renamed' }
    const { baseline, draft } = mergeIncoming(base, edited, inst())
    expect(draft.name).toBe('conflict-renamed')
    expect(changedFields(baseline, draft)).toEqual(['name'])
  })

  it('stops counting a change once the server holds that value', () => {
    // The answer to a save carries the new name; the form is clean again.
    const base = settingsFrom(inst())
    const edited = { ...base, name: 'conflict-renamed' }
    const { baseline, draft } = mergeIncoming(base, edited, inst({ name: 'conflict-renamed' }))
    expect(changedFields(baseline, draft)).toEqual([])
  })
})

describe('validate', () => {
  it('passes a sensible form', () => {
    expect(validate(settingsFrom(inst()))).toEqual({})
  })

  it('wants a name', () => {
    expect(validate({ ...settingsFrom(inst()), name: '   ' }).name).toBeTruthy()
  })

  it('wants real port numbers', () => {
    const errors = validate({ ...settingsFrom(inst()), a2s_port: 70000, rcon_port: null })
    expect(Object.keys(errors).sort()).toEqual(['a2s_port', 'rcon_port'])
  })

  it('refuses the same port twice', () => {
    expect(validate({ ...settingsFrom(inst()), rcon_port: 2001 }).game_port).toMatch(/own port/)
  })
})

describe('planSave', () => {
  it('asks for nothing when nothing changed', () => {
    const base = settingsFrom(inst())
    expect(planSave(base, { ...base })).toEqual([])
  })

  it('touches only the endpoints whose fields changed', () => {
    const base = settingsFrom(inst())
    const steps = planSave(base, { ...base, auto_start: false })
    expect(steps).toEqual([
      {
        key: 'restart',
        label: 'restart behaviour',
        path: '/restart-settings',
        body: { auto_restart: true, auto_start: false },
      },
    ])
  })

  it('renames first and changes the template after the ports', () => {
    const base = settingsFrom(inst())
    const draft = {
      ...base,
      name: ' renamed ',
      game_port: 2005,
      template_id: 2,
      restart_times: ['06:00'],
    }
    const steps = planSave(base, draft)
    expect(steps.map((s) => s.key)).toEqual(['basics', 'ports', 'template', 'schedule'])
    expect(steps[0].body).toEqual({ name: 'renamed', branch: 'stable' })
    expect(steps[0].label).toBe('name')
    // All three ports go together, so the backend checks them as a set.
    expect(steps[1].body).toEqual({ game_port: 2005, a2s_port: 17777, rcon_port: 19999 })
    expect(steps[3].body).toEqual({ times: ['06:00'] })
  })
})

describe('step labels', () => {
  it('name only what changed on the shared name/game version endpoint', () => {
    const base = settingsFrom(inst())
    expect(planSave(base, { ...base, branch: 'experimental' })[0].label).toBe('game version')
    expect(planSave(base, { ...base, name: 'x', branch: 'experimental' })[0].label).toBe(
      'name and game version',
    )
  })

  it('read like a sentence', () => {
    const label = (l) => ({ label: l })
    expect(describeSteps([label('ports')])).toBe('the ports')
    expect(describeSteps([label('name'), label('ports')])).toBe('the name and the ports')
    expect(describeSteps([label('name'), label('ports'), label('template')])).toBe(
      'the name, the ports and the template',
    )
  })
})
