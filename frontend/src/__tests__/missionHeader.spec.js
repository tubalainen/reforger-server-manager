// The paste box is the whole point of #162: whatever shape the user's mission
// header arrives in, it should end up as correct JSON without them having to
// hand-fix commas and indentation. Each test here is a shape that turned up in
// the research — AMP's box, a forum snippet, a truncated copy — plus the two
// rules the repairs must never break: syntax only, and always reported.
import { describe, expect, it } from 'vitest'

import {
  applyMerge,
  coerceValue,
  convertQuotedNumbers,
  countKeys,
  format,
  fromRows,
  parsePaste,
  previewMerge,
  quotedNumberKeys,
  toRows,
  valueKind,
} from '../missionHeader.js'
import { defaultValueFor, describeKey, searchKeys } from '../missionHeaderKeys.js'

const ok = (raw) => {
  const result = parsePaste(raw)
  expect(result.ok, result.error).toBe(true)
  return result
}

describe('parsePaste', () => {
  it('takes a plain object unchanged and reports no repairs', () => {
    const result = ok('{ "m_iPlayerCount": 96 }')
    expect(result.header).toEqual({ m_iPlayerCount: 96 })
    expect(result.repairs).toEqual([])
  })

  it("accepts AMP's brace-less body", () => {
    // Exactly what AMP's own Description gives as the example.
    const result = ok('"m_iPlayerCount":"64","m_eEditableGameFlags":"6"')
    expect(result.header).toEqual({ m_iPlayerCount: '64', m_eEditableGameFlags: '6' })
    expect(result.repairs.join(' ')).toMatch(/brace-less/)
  })

  it('fixes ragged indentation by rebuilding, not by editing text', () => {
    const result = ok(`{
    "m_sName": "Ops night",
          "m_iStartingHours": 5,
  "m_iStartingMinutes":45
        }`)
    expect(format(result.header)).toBe(
      '{\n  "m_sName": "Ops night",\n  "m_iStartingHours": 5,\n  "m_iStartingMinutes": 45\n}',
    )
  })

  it('closes braces a truncated copy is missing', () => {
    // Dragging a selection out of a scrolled textarea, which is how the ACE
    // block in the issue arrived.
    const result = ok(`{
      "m_ACE_Settings": {
        "m_ACE_Medical_Core": {
          "m_fBleedingRateScale": 0.6,
          "m_bBleedOutForPlayersEnabled": 1`)
    expect(result.header.m_ACE_Settings.m_ACE_Medical_Core).toEqual({
      m_fBleedingRateScale: 0.6,
      m_bBleedOutForPlayersEnabled: 1,
    })
    expect(result.repairs.join(' ')).toMatch(/Closed 3 unclosed braces/)
  })

  it('handles a paste that is cut off in the middle of a list', () => {
    // The realistic worst case, and the one from the issue's screenshot: AMP's
    // brace-less format, ragged indentation, a comment, a bare key, a quoted
    // number, and a copy that stops mid-block — so it ends on a comma with
    // nothing after it. The trailing comma only becomes visible once the wrap
    // and the missing braces are back, which is why that repair runs last.
    const result = ok(`// from the ACE thread
"m_iPlayerCount":"64",
m_eEditableGameFlags: 6,
   "m_ACE_Settings": {
     "m_ACE_Medical_Core": {
   "m_fDefaultResilienceRegenScale": 0.2,
        "m_fResilienceDamageScale": 0.9,`)
    expect(result.header).toEqual({
      m_iPlayerCount: '64',
      m_eEditableGameFlags: 6,
      m_ACE_Settings: {
        m_ACE_Medical_Core: {
          m_fDefaultResilienceRegenScale: 0.2,
          m_fResilienceDamageScale: 0.9,
        },
      },
    })
    expect(result.quotedNumberKeys).toEqual(['m_iPlayerCount'])
  })

  it('never invents a deletion when there are too many closing braces', () => {
    // A surplus closer means we misread the text; guessing could silently drop
    // half the user's settings, so it fails instead.
    expect(parsePaste('{"m_iPlayerCount": 64}}').ok).toBe(false)
  })

  it('adds the comma between two settings pasted together', () => {
    const result = ok(`{
      "m_iStartingHours": 8
      "m_iStartingMinutes": 30
      "m_sName": "Night"
      "m_bRandomStartingWeather": true
    }`)
    expect(result.header).toEqual({
      m_iStartingHours: 8,
      m_iStartingMinutes: 30,
      m_sName: 'Night',
      m_bRandomStartingWeather: true,
    })
    expect(result.repairs.join(' ')).toMatch(/missing commas/)
  })

  it('strips trailing commas, comments and quotes bare keys', () => {
    const result = ok(`{
      // starting time
      m_iStartingHours: 5,
      m_iStartingMinutes: 45,  /* quarter to six */
    }`)
    expect(result.header).toEqual({ m_iStartingHours: 5, m_iStartingMinutes: 45 })
    expect(result.repairs).toHaveLength(3)
  })

  it('repairs curly quotes from a forum copy', () => {
    const result = ok('{“m_sName”: “Everon”}')
    expect(result.header).toEqual({ m_sName: 'Everon' })
  })

  it('leaves string contents completely alone', () => {
    // The repairs must be structural. A rule text full of JSON-ish punctuation
    // is exactly the value a naive regex pass would mangle.
    const rules = 'No // teamkilling, /* ever */. Use {braces}, “quotes” and trailing commas,'
    const result = ok(JSON.stringify({ m_sDetails: rules }))
    expect(result.header.m_sDetails).toBe(rules)
    expect(result.repairs).toEqual([])
  })

  it('does not add a comma inside a multi-line-looking string value', () => {
    const result = ok('{"m_sDetails": "line one\\nline two", "m_iPlayerCount": 8}')
    expect(result.header.m_sDetails).toBe('line one\nline two')
  })

  it('pulls the header out of a whole config.json', () => {
    const result = ok(JSON.stringify({
      game: { name: 'srv', gameProperties: { missionHeader: { m_fXpMultiplier: 10 } } },
    }))
    expect(result.header).toEqual({ m_fXpMultiplier: 10 })
    expect(result.repairs.join(' ')).toMatch(/config\.json/)
  })

  it('pulls the header out of a bare "missionHeader" wrapper', () => {
    const result = ok('{"missionHeader": {"m_iPlayerCount": 32}}')
    expect(result.header).toEqual({ m_iPlayerCount: 32 })
  })

  it('keeps a lone "missionHeader"-named setting when it is not a wrapper', () => {
    const result = ok('{"missionHeader": {"a": 1}, "m_iPlayerCount": 32}')
    expect(result.header).toEqual({ missionHeader: { a: 1 }, m_iPlayerCount: 32 })
  })

  it('never reinterprets a value', () => {
    // AMP quotes its numbers, every other source writes them bare, and nobody
    // documents whether the engine coerces — so we change neither.
    const result = ok('{"m_iPlayerCount": "64", "m_fXpMultiplier": 10}')
    expect(result.header.m_iPlayerCount).toBe('64')
    expect(result.header.m_fXpMultiplier).toBe(10)
    expect(result.quotedNumberKeys).toEqual(['m_iPlayerCount'])
  })

  it('points at the spot when the engine tells us where it is', () => {
    // Two values with nothing between them, on one line — deliberately not the
    // newline-separated shape the missing-comma repair handles, because that
    // one is ambiguous mid-line.
    const result = parsePaste(`{
  "m_sName": "Ops",
  "m_iPlayerCount": 64 96
}`)
    expect(result.ok).toBe(false)
    expect(result.line).toBe(3)
    expect(result.error).toMatch(/line 3, column \d+/)
  })

  it('drops the echoed-back paste from an error message', () => {
    // V8 appends an excerpt of the input to some messages; the user is already
    // looking at it, and it makes the real reason hard to find.
    const result = parsePaste('{"m_sName": }')
    expect(result.ok).toBe(false)
    expect(result.error).not.toMatch(/is not valid JSON/)
    expect(result.error).toMatch(/Unexpected token/)
  })

  it('refuses an empty box and a runaway paste', () => {
    expect(parsePaste('   ').ok).toBe(false)
    expect(parsePaste(`{"m_sDetails": "${'x'.repeat(70_000)}"}`).ok).toBe(false)
  })

  it('refuses valid JSON that is not a set of settings', () => {
    expect(parsePaste('[1, 2, 3]').ok).toBe(false)
    expect(parsePaste('"just a string"').ok).toBe(false)
  })
})

describe('quoted numbers', () => {
  it('offers only top-level quoted numbers, and converts on request', () => {
    const header = { m_iPlayerCount: '64', m_sName: 'Base 12', m_sEmpty: '' }
    expect(quotedNumberKeys(header)).toEqual(['m_iPlayerCount'])
    expect(convertQuotedNumbers(header, ['m_iPlayerCount'])).toEqual({
      m_iPlayerCount: 64,
      m_sName: 'Base 12',
      m_sEmpty: '',
    })
  })
})

describe('merging', () => {
  const current = { m_iPlayerCount: 64, m_fXpMultiplier: 1 }

  it('previews what an insert would do', () => {
    expect(previewMerge(current, { m_iPlayerCount: 64, m_fXpMultiplier: 10, m_sName: 'x' })).toEqual({
      added: ['m_sName'],
      changed: ['m_fXpMultiplier'],
      unchanged: ['m_iPlayerCount'],
      removed: [],
    })
  })

  it('merges by default and replaces on request', () => {
    expect(applyMerge(current, { m_sName: 'x' })).toEqual({
      m_iPlayerCount: 64,
      m_fXpMultiplier: 1,
      m_sName: 'x',
    })
    expect(applyMerge(current, { m_sName: 'x' }, 'replace')).toEqual({ m_sName: 'x' })
  })
})

describe('the key catalog', () => {
  it('finds a setting from words the user would actually type', () => {
    // Nobody types m_iStartingHours; they type "starting hour".
    expect(searchKeys('starting hour').map((e) => e.key)).toContain('m_iStartingHours')
    // Matches the help text too, so "xp" also turns up the supply reward.
    expect(searchKeys('xp').map((e) => e.key)).toContain('m_fXpMultiplier')
    expect(searchKeys('supplies campaign').every((e) => e.group === 'campaign')).toBe(true)
  })

  it('hides settings already in the header', () => {
    expect(searchKeys('xp', ['m_fXpMultiplier']).map((e) => e.key)).not.toContain('m_fXpMultiplier')
  })

  it('starts a new setting on the engine default', () => {
    expect(defaultValueFor(describeKey('m_fXpMultiplier'))).toBe(1)
    expect(defaultValueFor(describeKey('m_eSaveTypes'))).toBe(15)
    expect(defaultValueFor(describeKey('m_bRandomStartingWeather'))).toBe(false)
    // A setting we know nothing about is still addable.
    expect(describeKey('m_ACE_Settings')).toBeUndefined()
    expect(defaultValueFor(undefined)).toBe('')
  })

  it('records what the time settings depend on', () => {
    expect(describeKey('m_iStartingHours').requires).toBe('m_bOverrideScenarioTimeAndWeather')
  })
})

describe('rows', () => {
  it('round-trips an object, keeping nested blocks as json rows', () => {
    const header = { m_iPlayerCount: 64, m_bRandom: true, m_sName: 'x', m_ACE: { a: 1 } }
    const rows = toRows(header)
    expect(rows.map((r) => r.kind)).toEqual(['number', 'boolean', 'string', 'json'])
    expect(fromRows(rows)).toEqual(header)
  })

  it('drops blank names and keeps the first of a clash', () => {
    expect(fromRows([
      { key: ' m_a ', value: 1 },
      { key: '', value: 2 },
      { key: 'm_a', value: 3 },
    ])).toEqual({ m_a: 1 })
  })

  it('converts a value when its type changes', () => {
    expect(coerceValue('64', 'number')).toBe(64)
    expect(coerceValue('nonsense', 'number')).toBe(0)
    expect(coerceValue('true', 'boolean')).toBe(true)
    expect(coerceValue(1, 'string')).toBe('1')
    expect(valueKind([1, 2])).toBe('json')
  })

  it('counts nested overrides too', () => {
    expect(countKeys({ a: 1, b: { c: 2, d: { e: 3 } } })).toBe(5)
    expect(countKeys(null)).toBe(0)
  })
})
