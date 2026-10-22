// Wording for a server's mod versions (#246), kept free of Vue for testing.

const STATE_LABELS = {
  behind: 'Update available',
  current: 'Up to date',
  locked: 'Locked',
  missing: 'Not downloaded',
  unreadable: 'Downloaded, version unknown',
  unknown: 'Workshop did not answer',
}

// A locked mod names its version: the template fixes it, whatever is on disk.
export function modStateLabel(state, locked = null) {
  if (state === 'locked' && locked) return `Locked at ${locked}`
  return STATE_LABELS[state] || state
}

// "12 mods: 9 up to date, 2 with an update, 1 locked."
export function modsSummary({ mods = [] }) {
  if (!mods.length) return ''
  const count = (s) => mods.filter((m) => m.state === s).length
  const parts = [
    [count('current'), 'up to date'],
    [count('behind'), 'with an update'],
    [count('locked'), 'locked'],
    [count('missing'), 'not downloaded yet'],
    [count('unreadable'), 'downloaded with no readable version'],
    [count('unknown'), 'not found on the Workshop'],
  ]
    .filter(([n]) => n)
    .map(([n, label]) => `${n} ${label}`)
  return `${mods.length} mod${mods.length === 1 ? '' : 's'}: ${parts.join(', ')}.`
}
