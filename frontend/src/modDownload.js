import { formatBytes } from './format'

// How far a starting server has got with downloading its mods (#235), from the
// `mod_download` block of its stats: the bytes of its mods on disk so far, and
// what the Workshop says the template's mods and their dependencies weigh. The
// server itself says nothing while it downloads, which is why its console looks
// stuck on a first start.
//
// The Workshop's sizes need not match what lands on disk, so the percentage is
// capped and "stopped growing" — not 100 % — is what ends the download.
//
// Returns { show, phase, percent, animated, label, detail }:
//   phase 'waiting'     nothing on disk yet
//   phase 'downloading' the folder is growing (or there is only one reading yet)
//   phase 'loading'     it stopped growing: the server is loading what it has
export function downloadView(md) {
  if (!md) return { show: false }
  if (md.plan_mods != null) return planView(md)
  const done = md.downloaded_bytes || 0
  const total = md.expected_state === 'ready' && md.expected_bytes > 0 ? md.expected_bytes : null
  const percent = total ? Math.min(100, Math.round((done / total) * 100)) : null
  const mods = modsNote(md)

  if (!done && !md.growing) {
    return {
      show: true,
      phase: 'waiting',
      percent: null,
      animated: true,
      label: 'Waiting for the mod download to start…',
      detail: [total && `${formatBytes(total)} to download`, mods].filter(Boolean).join(' · '),
    }
  }
  if (md.growing === false) {
    return {
      show: true,
      phase: 'loading',
      percent: 100,
      animated: false,
      label: 'Mods downloaded — loading the world…',
      detail: [`${formatBytes(done)} on disk`, mods].filter(Boolean).join(' · '),
    }
  }
  const speed = md.rate_bps > 0 ? `${formatBytes(md.rate_bps)}/s` : ''
  return {
    show: true,
    phase: 'downloading',
    percent,
    animated: true,
    label: total
      ? `Downloading mods: ${formatBytes(done)} of ${formatBytes(total)} · ${percent} %`
      : `Downloading mods: ${formatBytes(done)} so far`,
    detail: [speed, mods, lookupNote(md)].filter(Boolean).join(' · '),
  }
}

// With the run's plan (#246): only what this start has to fetch counts. Updating
// mods are rewritten in place, so they count once done; new ones as they fill.
function planView(md) {
  const n = md.plan_mods
  const mods = (k) => `${k} mod${k === 1 ? '' : 's'}`
  const loading = (label) => ({
    show: true, phase: 'loading', percent: 100, animated: false, label,
    detail: modsNote(md),
  })
  if (!n) return loading('Mods up to date — loading the world…')
  if (md.growing === false && md.plan_done >= n) {
    return loading(`${md.plan_kind === 'update' ? 'Mods updated' : 'Mods downloaded'} — loading the world…`)
  }
  const percent = md.plan_bytes > 0
    ? Math.min(100, Math.round((md.plan_done_bytes / md.plan_bytes) * 100))
    : Math.round((md.plan_done / n) * 100)
  const speed = md.rate_bps > 0 ? `${formatBytes(md.rate_bps)}/s` : ''
  const names = (md.plan_names || []).join(', ') + (n > (md.plan_names || []).length ? ', …' : '')
  if (md.plan_kind === 'update') {
    return {
      show: true, phase: 'downloading', percent, animated: true,
      label: `Updating ${mods(n)}: ${md.plan_done} of ${n} done`,
      detail: [names, speed].filter(Boolean).join(' · '),
    }
  }
  return {
    show: true, phase: 'downloading', percent, animated: true,
    label: `Downloading mods: ${formatBytes(md.plan_done_bytes)} of ${formatBytes(md.plan_bytes)} · ${percent} %`,
    detail: [speed, `${md.plan_done} of ${mods(n)} done`].filter(Boolean).join(' · '),
  }
}

function modsNote(md) {
  if (md.expected_state !== 'ready' || !md.mods) return ''
  const n = `${md.mods} mod${md.mods === 1 ? '' : 's'} with dependencies`
  return md.missing ? `${n} (${md.missing} not found on the Workshop)` : n
}

function lookupNote(md) {
  if (md.expected_state === 'resolving') return 'looking up the total size on the Workshop'
  if (md.expected_state === 'failed') return 'the Workshop did not answer, so the total is unknown'
  return ''
}
