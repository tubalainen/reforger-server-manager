import { describe, expect, it } from 'vitest'

import { guessCategory } from '../categoryGuess'

// The built-ins as a fresh install seeds them (models.BUILTIN_CATEGORIES).
const BUILTINS = ['Game Master', 'HQC', 'Conflict', 'COE', 'Escapist', 'Freedom Fighters'].map(
  (name, i) => ({ id: i + 1, name }),
)
const id = (name) => BUILTINS.find((c) => c.name === name).id

describe('guessCategory', () => {
  it('reads the base-game scenario files', () => {
    expect(guessCategory(BUILTINS, ['23_Campaign'])).toBe(id('Conflict'))
    expect(guessCategory(BUILTINS, ['21_GM_Eden'])).toBe(id('Game Master'))
    expect(guessCategory(BUILTINS, ['Conflict - Everon'])).toBe(id('Conflict'))
  })

  it('knows the other ways a built-in game mode is written', () => {
    expect(guessCategory(BUILTINS, ['Combat Ops Enhanced - Arland'])).toBe(id('COE'))
    expect(guessCategory(BUILTINS, ['CombatOpsEnhanced'])).toBe(id('COE'))
    expect(guessCategory(BUILTINS, ['COE2 Everon'])).toBe(id('COE'))
    expect(guessCategory(BUILTINS, ['GameMaster_Arland'])).toBe(id('Game Master'))
    expect(guessCategory(BUILTINS, ['HQCommander'])).toBe(id('HQC'))
    expect(guessCategory(BUILTINS, ['Escapists Arland'])).toBe(id('Escapist'))
    expect(guessCategory(BUILTINS, ['Freedom Fighter'])).toBe(id('Freedom Fighters'))
  })

  it('lets the longest match win, so a mod mode beats the Conflict it builds on', () => {
    expect(guessCategory(BUILTINS, ['Conflict HQ Commander Everon'])).toBe(id('HQC'))
  })

  it('looks at every text it is given', () => {
    expect(guessCategory(BUILTINS, ['My Saturday server', '', 'Escapist Plus'])).toBe(id('Escapist'))
  })

  it('matches whole words only', () => {
    expect(guessCategory(BUILTINS, ['Conflicted Coexistence'])).toBeNull()
    expect(guessCategory(BUILTINS, ['Programmer Hangout'])).toBeNull()
  })

  it('returns null when nothing matches or there is nothing to read', () => {
    expect(guessCategory(BUILTINS, ['Capture & Hold'])).toBeNull()
    expect(guessCategory(BUILTINS, [])).toBeNull()
    expect(guessCategory([], ['Conflict'])).toBeNull()
  })

  it("matches the user's own categories, and a renamed built-in by its new name", () => {
    const cats = [{ id: 7, name: 'Capture & Hold' }, { id: 8, name: 'Zeus' }]
    expect(guessCategory(cats, ['Arland Capture & Hold'])).toBe(7)
    expect(guessCategory(cats, ['21_GM_Eden'])).toBeNull()
  })
})

