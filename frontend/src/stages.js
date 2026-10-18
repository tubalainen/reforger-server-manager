// Release stages for server templates and mod lists (#223): Test → Dev → Prod.
// Kept free of Vue so the rules the pages share can be tested on their own.

export const STAGE_ORDER = ['prod', 'dev', 'test'] // how the list pages group them

export const STAGES = {
  test: {
    label: 'Test',
    cls: 'text-bg-secondary',
    hint: 'Free to change. Move it to Dev when it is worth testing properly.',
  },
  dev: {
    label: 'Dev',
    cls: 'text-bg-info',
    hint: 'Being tested. Changes are logged with a comment. Release it to Prod when it is ready.',
  },
  prod: {
    label: 'Prod',
    cls: 'text-bg-success',
    hint: 'Released and locked. Copy one to change it, then release the copy as its next version.',
  },
}

export function stageInfo(stage) {
  return STAGES[stage] || STAGES.test
}

// "v3" for a released template or mod list, '' for anything not in Prod.
export function versionLabel(item) {
  return item?.stage === 'prod' && item.prod_version ? `v${item.prod_version}` : ''
}

// Items grouped under Prod, Dev, Test (in that order), skipping empty groups.
// Within a group the incoming order (the API's, by name) is kept.
export function groupByStage(items) {
  return STAGE_ORDER
    .map((stage) => ({
      stage,
      ...stageInfo(stage),
      items: items.filter((i) => (STAGES[i.stage] ? i.stage : 'test') === stage),
    }))
    .filter((g) => g.items.length)
}

// The one move up a stage offers, or null in Prod.
export function promoteAction(stage) {
  if (stage === 'test') return { label: 'To Dev', release: false }
  if (stage === 'dev') return { label: 'Release…', release: true }
  return null
}

export const isLocked = (item) => item?.stage === 'prod'

// For the second delete prompt: what goes with a Prod item.
export function versionsText(n) {
  return n > 1 ? `all ${n} of its released versions` : 'its released version'
}

// Where a release goes by default: the Prod item this one was copied from, if
// it is still in Prod; otherwise a new Prod item (null).
export function defaultReleaseTarget(item, prods) {
  const id = item?.based_on?.id
  return id != null && prods.some((p) => p.id === id) ? id : null
}

// A warning when a copy is older than the Prod item it is about to replace:
// copied from v2 while v4 is out means v3 and v4's changes are not in it.
export function staleCopyWarning(item, target) {
  const from = item?.based_on
  if (!from || !target || from.id !== target.id || !from.version) return ''
  if (!target.prod_version || from.version >= target.prod_version) return ''
  return `This was copied from v${from.version}, but '${target.name}' is at v${target.prod_version} now. `
    + `Releasing it replaces v${target.prod_version}'s content with this copy's.`
}

// A Prod item's version picker: "Latest (v4)" first (null = follow new releases),
// then every released version, newest first, with its date and note (#223).
export function versionOptions(item) {
  if (!item || item.stage !== 'prod') return []
  const clip = (s) => (s.length > 40 ? `${s.slice(0, 39)}…` : s)
  return [
    { value: null, label: `Latest${item.prod_version ? ` (v${item.prod_version})` : ''}` },
    ...(item.versions || []).map((v) => ({
      value: v.version,
      label: [`v${v.version}`, v.released, v.note && clip(v.note)].filter(Boolean).join(' · '),
    })),
  ]
}

// The words for "release as new" vs "release into", for the dialog's choices.
export function releaseSummary(item, target, noun = 'template') {
  if (!target) return `'${item.name}' becomes a new Prod ${noun} at v1.`
  return `'${target.name}' gets v${(target.prod_version || 0) + 1} with this content, and '${item.name}' is used up.`
}
