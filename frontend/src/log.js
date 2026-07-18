// Server-log line classification (#108).
//
// Reforger's engine log tags severity per line as "COMPONENT (E): message"
// ((W) warnings, (E) errors, (F) fatals); FATAL and "error=" lines appear
// outside that scheme too. The log pane paints error lines red so they stand
// out of the scroll — warnings deliberately don't, the engine emits too many
// of them for amber to mean anything.
const ERROR_RE = /\((E|F)\)\s*:|\bFATAL\b|\bERROR\b/

export function isErrorLine(line) {
  return ERROR_RE.test(line || '')
}
