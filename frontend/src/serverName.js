// A distinguishable default name for a new template's server (#186).
//
// Every template used to start life as "Arma Reforger Server", so a server list
// full of them is exactly what the in-game browser showed — one prefilled field
// nobody edited, multiplied by everyone. The prefix stays (it is what people
// search for), and a two-word tag makes yours yours until you rename it.
//
// The words are deliberately dull and inoffensive: this ends up on a public
// server list under someone else's name, so nothing here is a joke, a reference
// or anything that reads badly beside a stranger's clan tag.

export const NAME_PREFIX = 'Arma Reforger Server'

export const ADJECTIVES = [
  'Alpine', 'Amber', 'Arctic', 'Bold', 'Brave', 'Bright', 'Coastal', 'Crimson',
  'Distant', 'Eastern', 'Golden', 'Granite', 'Hidden', 'Highland', 'Iron',
  'Lonely', 'Lucky', 'Northern', 'Quiet', 'Rapid', 'Restless', 'Rugged',
  'Rusty', 'Silent', 'Southern', 'Steady', 'Stormy', 'Swift', 'Western', 'Wild',
]

export const NOUNS = [
  'Anvil', 'Badger', 'Basin', 'Bastion', 'Beacon', 'Bison', 'Canyon', 'Compass',
  'Convoy', 'Falcon', 'Ferry', 'Foxhound', 'Harbour', 'Hollow', 'Lantern',
  'Lighthouse', 'Meadow', 'Otter', 'Outpost', 'Quarry', 'Ridge', 'Sentry',
  'Summit', 'Thicket', 'Trailhead', 'Valley', 'Wolf', 'Yardarm',
]

function pick(list) {
  return list[Math.floor(Math.random() * list.length)]
}

/** e.g. "Arma Reforger Server Hidden Ridge" — the prefilled name for a new template. */
export function randomServerName() {
  return `${NAME_PREFIX} ${pick(ADJECTIVES)} ${pick(NOUNS)}`
}
