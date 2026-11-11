import { describe, expect, it } from 'vitest'
import {
  compileErrorLines,
  editTemplateRoute,
  failingFromQuery,
  failureHeadline,
  failureReason,
  pulledInByText,
  pullingMods,
  rootCauses,
  unexplainedCompile,
} from '../modFailures'

const SCOPES = {
  name: 'WCS_Scopes',
  modId: '62A668F513428630',
  in_template: true,
  knock_on: false,
  needs: [
    { modId: '615806DC6C57AF02', name: 'WCS_NATO', state: 'not_installed' },
    { modId: '615818DA7C0343FD', name: 'WCS_RU', state: 'not_installed' },
  ],
}
const CUSTOMIZE = {
  name: 'Escapists WCS FORTEX Customize',
  modId: '6AAAAAAAAAAAAAAA',
  in_template: true,
  knock_on: true,
  needs: [{ modId: '62A668F513428630', name: 'WCS_Scopes', state: 'failed' }],
}

describe('failureReason', () => {
  it('names every missing dependency', () => {
    expect(failureReason(SCOPES)).toBe("Needs WCS_NATO and WCS_RU, which aren't installed.")
  })
  it('points a knock-on at the mod to fix first', () => {
    expect(failureReason(CUSTOMIZE)).toBe('Fails because WCS_Scopes failed to load: fix that one first.')
  })
  it('falls back to the id when the Workshop gave no name', () => {
    const m = { needs: [{ modId: 'ABCDEF0123456789', name: null, state: 'not_installed' }] }
    expect(failureReason(m)).toBe("Needs ABCDEF0123456789, which isn't installed.")
  })
})

describe('failureHeadline', () => {
  it('is empty for a start that did not fail', () => {
    expect(failureHeadline({ failed: false, mods: [SCOPES] })).toBe('')
  })
  it('names the causes and counts the knock-ons', () => {
    const f = { failed: true, mods: [SCOPES, CUSTOMIZE] }
    expect(rootCauses(f)).toEqual([SCOPES])
    expect(failureHeadline(f)).toBe(
      'The last start failed: WCS_Scopes could not be loaded, and 1 mod that needs it.',
    )
  })
  it('says when only scripts failed and the log names no mod', () => {
    expect(failureHeadline({ failed: true, mods: [], script_errors: ['x'] })).toMatch(/did not compile/)
  })
})

describe('a script that does not compile', () => {
  // The user's host (#270): the log gave the file, the mod's pak said whose it is.
  const error = {
    file: 'scripts/Game/Player/RO_V_PlayerController.c',
    line: 24,
    message: "Undefined function 'CharacterControllerComponent.SetGamepadControlAircraft'",
  }
  const vehicles = {
    name: 'Realism Overhaul - Vehicles',
    modId: '631C0CCC3F4506CB',
    in_template: true,
    knock_on: false,
    needs: [],
    reason: 'script',
    errors: [error],
    shared_with: [],
  }
  const owner = { modId: vehicles.modId, name: vehicles.name }

  it('names the mod in the headline', () => {
    expect(failureHeadline({ failed: true, mods: [vehicles] })).toBe(
      'The last start failed: Realism Overhaul - Vehicles has a script that does not compile.',
    )
  })
  it('gives the file, the line and the error', () => {
    expect(failureReason(vehicles)).toBe(
      'Its script scripts/Game/Player/RO_V_PlayerController.c (line 24) does not compile: ' +
        "Undefined function 'CharacterControllerComponent.SetGamepadControlAircraft'. " +
        'It is probably not updated for this game version.',
    )
  })
  it('says when another mod ships the same file', () => {
    const m = { ...vehicles, shared_with: [{ modId: 'ABCDEF0123456789', name: 'Other' }] }
    expect(failureReason(m)).toMatch(/Other ships the same file, so either may be the cause\.$/)
  })
  it('lists each error with the mod that ships its file', () => {
    const f = {
      failed: true,
      mods: [vehicles],
      compile_errors: [
        { ...error, root: true, owners: [owner] },
        { file: 'scripts/Game/SCR_X.c', line: 452, message: "Can't find class Tuple2", owners: [] },
      ],
    }
    expect(compileErrorLines(f)).toEqual([
      `${error.file}:24: ${error.message} — in Realism Overhaul - Vehicles`,
      "scripts/Game/SCR_X.c:452: Can't find class Tuple2",
    ])
    expect(unexplainedCompile(f)).toBe(false)
  })
  it('is unexplained when no mod ships the file', () => {
    const f = { failed: true, mods: [], script_errors: ['x'], compile_errors: [{ ...error, owners: [] }] }
    expect(unexplainedCompile(f)).toBe(true)
    expect(failureHeadline(f)).toMatch(/do not show which mod it belongs to/)
    expect(unexplainedCompile({ failed: true, mods: [SCOPES], compile_errors: [] })).toBe(false)
  })
})

describe('the template editor link', () => {
  it('opens the Mods step with the failing mods marked', () => {
    expect(editTemplateRoute(7, [SCOPES, CUSTOMIZE, { modId: null }])).toEqual({
      name: 'template-edit',
      params: { id: 7 },
      query: { step: 'mods', failing: '62A668F513428630,6AAAAAAAAAAAAAAA' },
    })
  })
  it('reads only well-formed ids back', () => {
    expect(failingFromQuery('62a668f513428630, nonsense,6AAAAAAAAAAAAAAA')).toEqual([
      '62A668F513428630',
      '6AAAAAAAAAAAAAAA',
    ])
    expect(failingFromQuery(undefined)).toEqual([])
  })
})

describe('a failing mod that is in no template', () => {
  // The user's host (#270): the server fetched WCS_Scopes for a template mod.
  const scopes = {
    ...SCOPES,
    in_template: false,
    pulled_in_by: [{ modId: '6A424768653033B3', name: 'Escapists RHS Customize faction' }],
  }
  const faction = { ...CUSTOMIZE, modId: '6A424768653033B3', pulled_in_by: [] }

  it('names the template mods that bring it in', () => {
    expect(pulledInByText(scopes)).toBe(
      'Not in your template: Escapists RHS Customize faction brings it in.',
    )
    expect(pulledInByText(faction)).toBe('')
  })
  it('does not mark a puller that is failing itself', () => {
    expect(pullingMods([scopes, faction])).toEqual([])
    expect(pullingMods([scopes]).map((p) => p.modId)).toEqual(['6A424768653033B3'])
  })
  it('sends the pullers to the template editor', () => {
    expect(editTemplateRoute(3, [scopes]).query).toEqual({
      step: 'mods',
      failing: '62A668F513428630',
      pulls: '6A424768653033B3',
    })
  })
})
