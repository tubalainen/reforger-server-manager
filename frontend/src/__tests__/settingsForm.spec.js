import { describe, expect, it } from 'vitest'

import {
  changedFields,
  describeSteps,
  LOCKED_WHILE_RUNNING,
  mergeIncoming,
  normaliseDays,
  normaliseTimes,
  pickTemplate,
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
        body: { auto_restart: true, auto_start: false, restart_unresponsive: false },
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
    expect(steps[3].body).toEqual({ times: ['06:00'], days: [], wait_empty_minutes: 0, backup: false })
  })

  it('saves mod updates on its own endpoint, after the restart times (#246)', () => {
    const base = settingsFrom(inst())
    expect(base.mod_auto_update).toBe(false)
    const steps = planSave(base, { ...base, restart_times: ['06:00'], mod_auto_update: true })
    expect(steps.map((s) => s.key)).toEqual(['schedule', 'mods'])
    expect(steps[1]).toEqual({
      key: 'mods',
      label: 'mod updates',
      path: '/mod-updates',
      body: { enabled: true },
    })
    expect(settingsFrom(inst({ mod_auto_update: true })).mod_auto_update).toBe(true)
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

describe('template versions (#223)', () => {
  it('reads the pin, null meaning latest', () => {
    expect(settingsFrom(inst()).template_version).toBeNull()
    expect(settingsFrom(inst({ template_version: 2 })).template_version).toBe(2)
  })

  it('saves a version-only change through the template step', () => {
    const base = settingsFrom(inst({ template_version: null }))
    const draft = { ...base, template_version: 1 }
    expect(changedFields(base, draft)).toEqual(['template_version'])
    const [step] = planSave(base, draft)
    expect(step).toMatchObject({
      key: 'template',
      label: 'template version',
      path: '/template',
      body: { template_id: 1, template_version: 1 },
    })
  })

  it('is locked while the server runs, like the template', () => {
    expect(LOCKED_WHILE_RUNNING).toContain('template_version')
  })

  it('starts another template at latest', () => {
    const draft = pickTemplate({ ...settingsFrom(inst()), template_version: 3 }, 2)
    expect(draft).toMatchObject({ template_id: 2, template_version: null })
  })
})

describe('restart options and limits (v0.95.0)', () => {
  const base = () => settingsFrom(inst())

  it('reads a server with none of them set', () => {
    const s = base()
    expect(s.restart_days).toEqual([])
    expect(s.restart_wait_empty_minutes).toBe(0)
    expect(s.restart_backup).toBe(false)
    expect(s.restart_unresponsive).toBe(false)
    expect(s.mem_limit_mb).toBeNull()
    expect(s.cpuset).toBe('')
  })

  it('treats all seven days as every day', () => {
    expect(normaliseDays([6, 5, 4, 3, 2, 1, 0])).toEqual([])
    expect(normaliseDays([4, 0, 4, 9])).toEqual([0, 4])
  })

  it('saves the days, the wait and the backup with the times, in one request', () => {
    const steps = planSave(base(), { ...base(), restart_days: [0, 4], restart_backup: true })
    expect(steps).toHaveLength(1)
    expect(steps[0].path).toBe('/schedule')
    expect(steps[0].body).toMatchObject({ days: [0, 4], wait_empty_minutes: 0, backup: true })
    expect(steps[0].body.times).toEqual(base().restart_times)
  })

  it('saves the freeze switch with the other restart switches', () => {
    const [step] = planSave(base(), { ...base(), restart_unresponsive: true })
    expect(step.path).toBe('/restart-settings')
    expect(step.body.restart_unresponsive).toBe(true)
  })

  it('saves the limits on their own, an emptied box as no limit', () => {
    const [step] = planSave(base(), { ...base(), mem_limit_mb: 8192, cpuset: '0-3, 6' })
    expect(step).toMatchObject({ path: '/resources', body: { mem_limit_mb: 8192, cpuset: '0-3,6' } })
    const withLimit = settingsFrom(inst({ mem_limit_mb: 8192 }))
    const [cleared] = planSave(withLimit, { ...withLimit, mem_limit_mb: null })
    expect(cleared.body.mem_limit_mb).toBeNull()
  })

  it('refuses a limit that would kill the server and a CPU list that is not one', () => {
    expect(validate({ ...base(), mem_limit_mb: 512 }).mem_limit_mb).toBeTruthy()
    expect(validate({ ...base(), cpuset: '0-' }).cpuset).toBeTruthy()
    expect(validate({ ...base(), mem_limit_mb: 4096, cpuset: '0,2-3' })).toEqual({})
  })
})
