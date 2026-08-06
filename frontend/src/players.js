// Pure helpers for the three player access lists a template carries (#154):
// the server admins (game.admins), the whitelist (game.playerWhitelist) and the
// ban list (game.playerBanList).
//
// Two different kinds of id show up here, and mixing them up is the mistake
// that quietly costs an admin their rights:
//
//   * Steam64  — 17 digits, e.g. 76561198000000000. Accepted in `admins` only.
//   * identity — a Bohemia *account* id in UUID form, e.g.
//                7f9b0a4c-1d2e-4f6a-8b3c-9d0e1f2a3b4c. Accepted everywhere, and
//                the only kind the whitelist and ban list match on.
//
// The game accepts at most 20 admins; past that the list stops being applied,
// so the wizard warns rather than silently letting it grow.

export const ADMIN_LIMIT = 20

const STEAM64_RE = /^\d{17}$/
const IDENTITY_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

// 'steam' | 'identity' | null (null = we don't recognise it)
export function idKind(raw) {
  const value = String(raw ?? '').trim()
  if (STEAM64_RE.test(value)) return 'steam'
  if (IDENTITY_RE.test(value)) return 'identity'
  return null
}

// Split a pasted blob into candidate ids: one per line, or separated by commas,
// semicolons or spaces — so a list copied out of a spreadsheet or a Discord
// message can be added in one go, the way mods can be (#104).
export function splitIds(raw) {
  return String(raw ?? '')
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function sameId(a, b) {
  return String(a).toLowerCase() === String(b).toLowerCase()
}

// Add ids to the admin list, reporting exactly what happened to each one rather
// than failing the whole paste — same partial-success contract as adding mods.
// -> { admins, added: [], duplicates: [], invalid: [], overflow: [] }
export function addAdmins(current, raw) {
  const admins = [...(current || [])]
  const added = []
  const duplicates = []
  const invalid = []
  const overflow = []
  for (const id of splitIds(raw)) {
    if (!idKind(id)) {
      invalid.push(id)
    } else if (admins.some((a) => sameId(a, id))) {
      duplicates.push(id)
    } else if (admins.length >= ADMIN_LIMIT) {
      overflow.push(id)
    } else {
      admins.push(id)
      added.push(id)
    }
  }
  return { admins, added, duplicates, invalid, overflow }
}

export function removeAdmin(current, id) {
  return (current || []).filter((a) => !sameId(a, id))
}

// A whitelist/ban entry the backend will accept: identityId is what the server
// matches on, name is a label for whoever reads the list later.
export function normalizePlayer(entry, { reason = false } = {}) {
  const player = {
    identityId: String(entry?.identityId ?? '').trim(),
    name: String(entry?.name ?? '').trim(),
  }
  if (reason) player.reason = String(entry?.reason ?? '').trim()
  return player
}

// -> { players, added: [], duplicates: [], invalid: [] }
// `invalid` holds ids that aren't identity ids at all — including Steam64s,
// which are valid for admins but are NOT what these two lists match on.
// `withReason` builds ban entries (identityId/name/reason); without it, plain
// whitelist entries (identityId/name).
export function addPlayers(current, raw, { name = '', reason = '', withReason = false } = {}) {
  const players = [...(current || [])]
  const added = []
  const duplicates = []
  const invalid = []
  const ids = splitIds(raw)
  // A typed name/reason describes one player: applying it to every id of a
  // pasted batch would label them all with one person's story.
  const single = ids.length === 1
  for (const id of ids) {
    if (idKind(id) !== 'identity') {
      invalid.push(id)
    } else if (players.some((p) => sameId(p.identityId, id))) {
      duplicates.push(id)
    } else {
      players.push(normalizePlayer(
        { identityId: id, name: single ? name : '', reason: single ? reason : '' },
        { reason: withReason },
      ))
      added.push(id)
    }
  }
  return { players, added, duplicates, invalid }
}

export function removePlayer(current, identityId) {
  return (current || []).filter((p) => !sameId(p.identityId, identityId))
}

// One human sentence covering everything a multi-add skipped, or '' when it all
// landed — the wizard shows this instead of failing silently (#106).
export function addNotice({ added = [], duplicates = [], invalid = [], overflow = [] }) {
  const parts = []
  if (added.length) parts.push(`Added ${added.length}`)
  if (duplicates.length) parts.push(`${duplicates.length} already listed`)
  if (invalid.length) parts.push(`${invalid.length} not a valid id: ${invalid.join(', ')}`)
  if (overflow.length) {
    parts.push(`${overflow.length} skipped — the game applies at most ${ADMIN_LIMIT} admins`)
  }
  return parts.join(' · ')
}
