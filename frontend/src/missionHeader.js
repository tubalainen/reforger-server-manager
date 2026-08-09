// Pure helpers for the mission header overrides a template carries (#162) —
// game.gameProperties.missionHeader, the block that overrides the scenario's own
// SCR_MissionHeader (and whatever a mod derives from it).
//
// The load-bearing piece is parsePaste(): people arrive here with text copied
// out of a forum post, a Discord message, someone else's config.json, or AMP's
// Mission Header box — which holds the *body* of the object with no braces
// around it, because AMP interpolates the text straight into its config
// template. All of it should just work.
//
// So this module is deliberately lenient on the way in and strict on the way
// out: repair what is unambiguously a formatting mistake, parse to an object,
// and let the caller re-render. We never splice the user's text into the config
// — that is precisely the AMP failure mode where one stray comma writes a
// config.json the engine can't read.
//
// Two rules the repairs never break:
//   1. Syntax only. No value is ever reinterpreted — "64" stays the string
//      "64", 64 stays the number. Nothing documents whether the engine coerces,
//      so that call belongs to the user (see quotedNumberKeys).
//   2. Every repair is reported. The dialog shows what changed before anything
//      is inserted; silent fixes are how you lose text you meant to keep.

// Kept in step with MISSION_HEADER_MAX_BYTES in
// backend/services/template_service.py, so an oversized paste is refused here
// with a readable message instead of coming back as a validation error.
export const MAX_BYTES = 64 * 1024

// ---- string-aware plumbing --------------------------------------------------

// Every repair below has to know which characters are *inside* a JSON string: a
// `//` in a server-rules value is not a comment, and a `{` there is not a brace
// to balance. Running plain regexes over the whole document is the classic way
// to corrupt somebody's text.
//
// Returns a mask where 1 = this character is string content. The quotes
// themselves are 0, so a pattern is free to match a string's delimiters (the
// missing-comma repair needs exactly that) while never matching through one.
function stringMask(text) {
  const mask = new Uint8Array(text.length)
  let inString = false
  let escaped = false
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    if (inString) {
      if (escaped) {
        escaped = false
      } else if (ch === '\\') {
        escaped = true
      } else if (ch === '"') {
        inString = false
        continue // the closing quote is a delimiter, not content
      }
      mask[i] = 1
      continue
    }
    if (ch === '"') inString = true
  }
  return mask
}

// Apply `pattern` only where the match touches no string content. Matches are
// rebuilt in one pass so offsets stay valid.
function replaceStructural(text, pattern, replacer) {
  const mask = stringMask(text)
  let out = ''
  let last = 0
  for (const match of text.matchAll(pattern)) {
    const start = match.index
    const end = start + match[0].length
    let masked = false
    for (let i = start; i < end && !masked; i += 1) masked = mask[i] === 1
    if (masked) continue
    out += text.slice(last, start) + replacer(...match)
    last = end
  }
  return out + text.slice(last)
}

// ---- the individual repairs -------------------------------------------------

// Invisible junk that survives a copy out of a web page or a chat client and
// then breaks JSON.parse with a baffling message. Typographic quotes are the
// common one: a forum post renders "m_sName" with curly quotes.
// Written as escapes on purpose: these characters are invisible in an editor,
// so spelling them out is the only way the next reader can tell what this does.
const INVISIBLES = [
  [/^\uFEFF/, ''],                                 // byte order mark
  [/[\u00A0\u2007\u202F]/g, ' '],                  // non-breaking spaces
  [/[\u200B-\u200D\u2060]/g, ''],                  // zero-width spaces and joiners
  [/[\u201C\u201D\u201E\u201F\u2033]/g, '"'],  // curly and prime double quotes
  [/[\u2018\u2019\u201A\u201B\u2032]/g, "'"],  // curly and prime single quotes
]

function stripComments(text) {
  let out = ''
  let mode = null // null | 'line' | 'block'
  let inString = false
  let escaped = false
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    const next = text[i + 1]
    if (mode === 'line') {
      if (ch === '\n') {
        mode = null
        out += ch
      }
      continue
    }
    if (mode === 'block') {
      if (ch === '*' && next === '/') {
        mode = null
        i += 1
      }
      continue
    }
    if (inString) {
      out += ch
      if (escaped) escaped = false
      else if (ch === '\\') escaped = true
      else if (ch === '"') inString = false
      continue
    }
    if (ch === '"') {
      inString = true
      out += ch
      continue
    }
    if (ch === '/' && next === '/') { mode = 'line'; i += 1; continue }
    if (ch === '/' && next === '*') { mode = 'block'; i += 1; continue }
    out += ch
  }
  return out
}

// 'text' -> "text". Runs before anything that relies on stringMask, because a
// single-quoted string would otherwise confuse the mask itself.
function singleToDoubleQuotes(text) {
  let out = ''
  let inDouble = false
  let escaped = false
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    if (inDouble) {
      out += ch
      if (escaped) escaped = false
      else if (ch === '\\') escaped = true
      else if (ch === '"') inDouble = false
      continue
    }
    if (ch === '"') { inDouble = true; out += ch; continue }
    if (ch === "'") {
      const end = text.indexOf("'", i + 1)
      if (end === -1) { out += ch; continue }
      out += `"${text.slice(i + 1, end).replace(/"/g, '\\"')}"`
      i = end
      continue
    }
    out += ch
  }
  return out
}

// m_iPlayerCount: 64  ->  "m_iPlayerCount": 64
// Key position only. A bare word anywhere else is left alone, so the parse
// still fails loudly rather than us inventing a string the user never wrote.
function quoteBareKeys(text) {
  return replaceStructural(
    text,
    /([{,]\s*)([A-Za-z_$][\w$]*)(\s*:)/g,
    (_m, before, key, after) => `${before}"${key}"${after}`,
  )
}

// Two settings pasted together with no comma between them:
//     "m_iStartingHours": 8
//     "m_iStartingMinutes": 30
// A value ends (a closing quote, a digit, } or ], or the last letter of
// true/false/null) and the next line opens a new key. Newline-separated only —
// on a single line, a truncated value looks too much like a missing comma.
function insertMissingCommas(text) {
  return replaceStructural(
    text,
    /(["\d}\]el])(\s*\r?\n\s*)(")/g,
    (_m, end, gap, quote) => `${end},${gap}${quote}`,
  )
}

function stripTrailingCommas(text) {
  return replaceStructural(text, /,(\s*[}\]])/g, (_m, after) => after)
}

// Append the closers a truncated copy is missing — the usual result of dragging
// a selection out of a scrolled textarea, which is how the ACE settings block
// in the issue arrived. Only ever appends: a *surplus* closing brace means we
// have misread the text, and inventing a deletion there could silently drop
// half of somebody's settings.
function balanceBrackets(text) {
  const mask = stringMask(text)
  const stack = []
  let surplus = false
  for (let i = 0; i < text.length; i += 1) {
    if (mask[i]) continue
    const ch = text[i]
    if (ch === '{') stack.push('}')
    else if (ch === '[') stack.push(']')
    else if (ch === '}' || ch === ']') {
      if (stack.length && stack[stack.length - 1] === ch) stack.pop()
      else surplus = true
    }
  }
  if (surplus || !stack.length) return { text, added: 0 }
  const closers = stack.reverse().join('')
  return { text: text.replace(/\s*$/, '') + closers, added: closers.length }
}

// Does this look like AMP's brace-less body — `"a": 1, "b": 2` — rather than a
// whole object? Checked after the other repairs, so a body that also had
// comments or bare keys is still recognised.
function looksLikeBody(text) {
  const trimmed = text.trim()
  if (!trimmed || trimmed.startsWith('{')) return false
  return /^"[^"]*"\s*:/.test(trimmed) || /^[A-Za-z_$][\w$]*\s*:/.test(trimmed)
}

// A whole config.json, a `"missionHeader": {...}` fragment, or the header
// object itself — return the header either way.
function unwrap(value) {
  if (!isPlainObject(value)) return { value, unwrapped: null }
  if (isPlainObject(value?.game?.gameProperties?.missionHeader)) {
    return { value: value.game.gameProperties.missionHeader, unwrapped: 'config.json' }
  }
  if (isPlainObject(value?.gameProperties?.missionHeader)) {
    return { value: value.gameProperties.missionHeader, unwrapped: 'gameProperties block' }
  }
  if (isPlainObject(value.missionHeader) && Object.keys(value).length === 1) {
    return { value: value.missionHeader, unwrapped: '"missionHeader" wrapper' }
  }
  return { value, unwrapped: null }
}

export function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

// ---- the pipeline -----------------------------------------------------------

/**
 * Parse pasted text into a mission header object, repairing what is safely
 * repairable and reporting every repair.
 *
 * @returns {{ok: true, header: object, repairs: string[], quotedNumberKeys: string[]}}
 *        | {ok: false, error: string, line: number|null, column: number|null}
 */
export function parsePaste(raw) {
  const source = String(raw ?? '')
  if (!source.trim()) {
    return { ok: false, error: 'Nothing to insert — the box is empty.', line: null, column: null }
  }
  if (source.length > MAX_BYTES) {
    return {
      ok: false,
      error: `That is ${Math.round(source.length / 1024)} KB of text; the limit is ${MAX_BYTES / 1024} KB.`,
      line: null,
      column: null,
    }
  }

  // If it already parses, touch nothing. Repairs exist for text that doesn't,
  // and running them over valid JSON is how you corrupt a value that happens to
  // be full of JSON-ish punctuation — a server-rules string with braces, curly
  // quotes and trailing commas in it is a completely normal thing to write.
  try {
    return finish(JSON.parse(source), [])
  } catch {
    // fall through to the repairs
  }

  const repairs = []
  let text = source

  const step = (next, note) => {
    if (next !== text) {
      repairs.push(note)
      text = next
    }
  }

  step(
    INVISIBLES.reduce((acc, [pattern, to]) => acc.replace(pattern, to), text),
    'Replaced curly quotes and invisible characters left behind by copy-paste',
  )
  step(stripComments(text), 'Removed comments — JSON has none')
  step(singleToDoubleQuotes(text), 'Changed \'single quotes\' to "double quotes"')
  step(quoteBareKeys(text), 'Put quotes around unquoted setting names')
  step(insertMissingCommas(text), 'Added missing commas between settings')

  if (looksLikeBody(text)) {
    repairs.push('Wrapped the settings in { } — this was pasted in AMP’s brace-less format')
    text = `{${text}}`
  }

  const balanced = balanceBrackets(text)
  if (balanced.added) {
    repairs.push(
      `Closed ${balanced.added} unclosed brace${balanced.added === 1 ? '' : 's'} — the paste looked cut short`,
    )
    text = balanced.text
  }

  // Last, not earlier: a paste that was cut off mid-list ends on a comma with
  // no closer after it, so the trailing comma only becomes visible once the
  // wrap and the missing braces have been put back.
  step(stripTrailingCommas(text), 'Removed trailing commas')

  try {
    return finish(JSON.parse(text), repairs)
  } catch (e) {
    return { ok: false, ...describeSyntaxError(e, text) }
  }
}

// Shared tail: unwrap whatever the header was pasted inside, then check it is
// something we can actually store. Reached both by the "already valid" fast path
// and by the repaired one, so the two can't drift apart.
function finish(parsed, repairs) {
  const { value, unwrapped } = unwrap(parsed)
  if (unwrapped) repairs = [...repairs, `Pulled the mission header out of the surrounding ${unwrapped}`]

  if (!isPlainObject(value)) {
    return {
      ok: false,
      error: 'That is valid JSON, but not a set of settings — expected "name": value pairs.',
      line: null,
      column: null,
    }
  }
  if (JSON.stringify(value).length > MAX_BYTES) {
    return {
      ok: false,
      error: `That mission header is over the ${MAX_BYTES / 1024} KB limit.`,
      line: null,
      column: null,
    }
  }
  return { ok: true, header: value, repairs, quotedNumberKeys: quotedNumberKeys(value) }
}

// Turn JSON.parse's message into something worth showing a person.
//
// V8 has two shapes, and which one you get depends on the length of the input:
// long documents give `… in JSON at position 41 (line 3 column 7)`, short ones
// give `Unexpected token '}', "{"a": }" is not valid JSON` — where the quoted
// part is just the paste echoed back, which the user is already looking at. So
// keep the reason, drop the noise, and add a line/column when there is one.
function describeSyntaxError(err, text) {
  const message = String(err?.message ?? 'Could not read that as JSON')
  const reason = message
    .replace(/\s*at position \d+.*$/, '')
    .replace(/,\s*"[\s\S]*"\s*is not valid JSON\.?$/, '')
    .replace(/\s*in JSON$/, '')
    .trim()
  const at = /at position (\d+)/.exec(message)
  if (!at) return { error: `${reason}.`, line: null, column: null }
  const before = text.slice(0, Number(at[1]))
  const line = before.split('\n').length
  const column = before.length - before.lastIndexOf('\n')
  return { error: `${reason} — line ${line}, column ${column}.`, line, column }
}

// Top-level settings whose value is a quoted number, e.g. AMP's own example
// "m_iPlayerCount":"64". Offered as a one-click conversion, never applied on our
// own: nothing documents whether the engine coerces, so it stays the user's call.
export function quotedNumberKeys(header) {
  if (!isPlainObject(header)) return []
  return Object.keys(header).filter((key) => {
    const value = header[key]
    return typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))
  })
}

export function convertQuotedNumbers(header, keys) {
  const out = { ...header }
  for (const key of keys) out[key] = Number(out[key])
  return out
}

// ---- merging ----------------------------------------------------------------

/** What applying `incoming` to `current` would do, for the dialog's preview. */
export function previewMerge(current, incoming) {
  const base = isPlainObject(current) ? current : {}
  const added = []
  const changed = []
  const unchanged = []
  for (const [key, value] of Object.entries(incoming)) {
    if (!(key in base)) added.push(key)
    else if (JSON.stringify(base[key]) === JSON.stringify(value)) unchanged.push(key)
    else changed.push(key)
  }
  return {
    added,
    changed,
    unchanged,
    // Only meaningful for "replace everything": what merging would keep.
    removed: Object.keys(base).filter((key) => !(key in incoming)),
  }
}

export function applyMerge(current, incoming, mode = 'merge') {
  const base = isPlainObject(current) ? current : {}
  return mode === 'replace' ? { ...incoming } : { ...base, ...incoming }
}

// ---- rows <-> object --------------------------------------------------------

// The row editor's model. `kind` picks the input; 'json' covers nested blocks (a
// mod's settings tree) and anything else that doesn't fit one field — those are
// edited as JSON rather than flattened into rows that lie about the structure.
export function valueKind(value) {
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number') return 'number'
  if (typeof value === 'string') return 'string'
  return 'json'
}

export function toRows(header) {
  if (!isPlainObject(header)) return []
  return Object.entries(header).map(([key, value]) => ({ key, value, kind: valueKind(value) }))
}

/** Rows back to an object. Blank names are dropped; the first of a clash wins. */
export function fromRows(rows) {
  const out = {}
  for (const row of rows) {
    const key = String(row?.key ?? '').trim()
    if (!key || key in out) continue
    out[key] = row.value
  }
  return out
}

/** Convert a row's value when the user switches its type, losing as little as possible. */
export function coerceValue(value, kind) {
  if (kind === 'boolean') {
    return value === true || value === 'true' || value === 1 || value === '1'
  }
  if (kind === 'number') {
    const n = Number(value)
    return Number.isFinite(n) ? n : 0
  }
  if (kind === 'string') {
    return typeof value === 'string' ? value : JSON.stringify(value ?? '')
  }
  return value
}

/** Pretty-print for the JSON view and the Format button. */
export function format(header) {
  return JSON.stringify(isPlainObject(header) ? header : {}, null, 2)
}

/** Every override, including the ones nested inside a mod's own block. */
export function countKeys(header) {
  if (!isPlainObject(header)) return 0
  return Object.values(header).reduce(
    (total, value) => total + (isPlainObject(value) ? countKeys(value) : 0),
    Object.keys(header).length,
  )
}
