import { describe, expect, it } from 'vitest'

import { ADJECTIVES, NAME_PREFIX, NOUNS, randomServerName } from '../serverName'

describe('randomServerName', () => {
  it('keeps the prefix people search for and adds two words', () => {
    const name = randomServerName()
    expect(name.startsWith(`${NAME_PREFIX} `)).toBe(true)
    const [adjective, noun] = name.slice(NAME_PREFIX.length + 1).split(' ')
    expect(ADJECTIVES).toContain(adjective)
    expect(NOUNS).toContain(noun)
  })

  it('does not hand everyone the same name', () => {
    // The whole point (#186): 200 draws from ~800 combinations must not collapse
    // onto one string the way the old constant did.
    const seen = new Set(Array.from({ length: 200 }, randomServerName))
    expect(seen.size).toBeGreaterThan(20)
  })

  it('offers no word that would read badly on a public server list', () => {
    for (const word of [...ADJECTIVES, ...NOUNS]) {
      expect(word).toMatch(/^[A-Z][a-z]+$/)
    }
  })
})
