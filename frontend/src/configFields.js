// The template wizard's Advanced settings, built from the backend's field
// catalog (GET /api/templates/fields). The catalog says where each setting is
// shown, its config.json key and what it does; its limits and defaults are the
// template model's own, so the wizard can't drift from what a save accepts.
// These helpers are the pure part: searching it and comparing against defaults.

// Does a setting match what was typed in the filter box? Label, config key,
// full path and help text all count, so "VONCanTransmitCrossFaction",
// "cross-faction" and "radio" all find the same switch.
export function matchesField(field, query) {
  const q = (query || '').trim().toLowerCase()
  if (!q) return true
  return [field.label, field.key, field.path, field.help].some((text) =>
    (text || '').toLowerCase().includes(q),
  )
}

// The catalog's groups, each holding its matching Advanced settings, in
// catalog order. Groups with nothing to show are left out.
export function advancedGroups(catalog, query) {
  if (!catalog) return []
  return catalog.groups
    .map((group) => ({
      ...group,
      fields: catalog.fields.filter(
        (f) => f.card === 'advanced' && f.group === group.id && matchesField(f, query),
      ),
    }))
    .filter((group) => group.fields.length)
}

// Settings shown on another card that match a typed query, so a search for
// "battleye" says where BattlEye is instead of "no match". Empty without a query.
export function elsewhereMatches(catalog, query) {
  if (!catalog || !(query || '').trim()) return []
  return catalog.fields.filter((f) => f.card !== 'advanced' && matchesField(f, query))
}

// Is this value something other than the engine default? A blank text field and
// an empty default are the same thing.
export function isChanged(field, value) {
  if (field.kind === 'text') return (value ?? '') !== (field.default ?? '')
  if (field.kind === 'list') return JSON.stringify(value || []) !== JSON.stringify(field.default || [])
  return value !== field.default
}

// A list setting is edited one entry per line: trimmed, blanks dropped, each
// entry once — the same cleaning the backend applies on save.
export function linesToList(text) {
  const out = []
  for (const line of (text || '').split('\n')) {
    const entry = line.trim()
    if (entry && !out.includes(entry)) out.push(entry)
  }
  return out
}

export function listToLines(list) {
  return Array.isArray(list) ? list.join('\n') : ''
}

// How many Advanced settings differ from their default, for the card's header.
export function changedCount(catalog, spec) {
  if (!catalog) return 0
  return catalog.fields.filter((f) => f.card === 'advanced' && isChanged(f, spec[f.name])).length
}

// min/max for an input, from the catalog. {} until the catalog has loaded.
export function limitsFor(catalog, name) {
  const field = catalog?.fields.find((f) => f.name === name)
  if (!field) return {}
  const out = {}
  if (field.min != null) out.min = field.min
  if (field.max != null) out.max = field.max
  return out
}
