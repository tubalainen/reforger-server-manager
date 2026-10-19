// Guess a new template's game-mode category (#232) from what the wizard knows
// about it: the template name, the scenario and the Workshop mod behind it.
//
// A category matches on its own name, and the built-ins (models.BUILTIN_CATEGORIES)
// also on the other ways their game mode gets written. A rename keeps the user's
// own name working; only the aliases are tied to the built-in names.

const ALIASES = {
  'game master': ['gamemaster', 'gm'],
  hqc: ['hq commander', 'headquarters commander', 'hqcommander'],
  conflict: ['campaign'],
  coe: ['combat ops enhanced', 'combatopsenhanced'],
  escapist: ['escapists'],
  'freedom fighters': ['freedom fighter', 'freedomfighters'],
}

// Lower case, camelCase split ("GameMaster" → "game master") and every run of
// punctuation, underscores or spaces collapsed to one space, padded so a term
// can be matched as whole words with a plain substring search.
function normalise(text) {
  const words = String(text || '')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
  return words ? ` ${words} ` : ''
}

// A term matches as whole words, optionally followed by digits ("COE2"). Both
// sides are normalised, so the term holds only [a-z0-9 ] and needs no escaping.
function matches(haystack, term) {
  return new RegExp(` ${term}\\d* `).test(haystack)
}

function termsFor(category) {
  const name = String(category.name || '').trim()
  return [name, ...(ALIASES[name.toLowerCase()] || [])].filter(Boolean)
}

/**
 * The id of the category the texts point at, or null when none does.
 * The longest matching term wins, so "Conflict HQ Commander" files under HQC,
 * not Conflict; a tie goes to the category listed first.
 */
export function guessCategory(categories, texts) {
  const haystack = normalise((texts || []).filter(Boolean).join(' | '))
  if (!haystack) return null
  let best = null
  let bestLength = 0
  for (const c of categories || []) {
    for (const term of termsFor(c)) {
      const t = normalise(term).trim()
      if (t.length > bestLength && matches(haystack, t)) {
        best = c.id
        bestLength = t.length
      }
    }
  }
  return best
}

