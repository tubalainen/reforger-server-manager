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

// What the engine did with the order of config.json's mods at the last start (#257),
// in one sentence, from GET /load-order.
export function loadOrderSummary(o) {
  if (!o?.available) return ''
  let how
  if (o.relation === 'same') how = 'in the order of your list'
  else if (o.relation === 'reversed') how = 'in the reverse of the order of your list'
  else if (o.relation === 'unknown') how = 'in an order there was too little to compare'
  else if (o.inverted * 2 > o.pairs) {
    how = `mostly in reverse of your list (${o.inverted} of ${o.pairs} pairs swapped)`
  } else how = `mostly in the order of your list (${o.inverted} of ${o.pairs} pairs swapped)`
  const mods = o.mounted.filter((m) => !m.base_game).length
  const extra = o.unlisted
    ? ` ${o.unlisted} of them ${o.unlisted === 1 ? 'is' : 'are'} not in your list: ` +
      `${o.unlisted === 1 ? 'a dependency' : 'dependencies'} the server fetched itself.`
    : ''
  return `The engine loaded ${mods} mod${mods === 1 ? '' : 's'} ${how}, then the base game.${extra}`
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
