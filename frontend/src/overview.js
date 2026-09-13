// The Servers overview's derived views (#189): the host-wide "needs attention" list
// and the sparkline geometry. Kept free of Vue so both can be tested on their own.

const BRANCH_LABEL = { stable: 'Stable', experimental: 'Experimental' }

export function branchLabel(branch) {
  return BRANCH_LABEL[branch] || branch
}

// "Alpha", "Alpha and Bravo", "Alpha, Bravo and Charlie"
export function joinNames(names) {
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/**
 * Everything on the host that is waiting for the operator, one item per problem,
 * each carrying the single action that fixes it.
 *
 * @param servers  summary.servers from /api/instances/summary
 * @param updates  branches from /api/serverfiles/auto-update with update_available
 * @param autoDownload  whether the manager downloads new server files by itself
 * @returns [{ key, subject, text, action }] where action is
 *   { kind: 'restart', id } | { kind: 'update', branch } | { kind: 'server-files' } | null
 */
export function attentionItems(servers = [], updates = [], autoDownload = false) {
  const items = []

  // A running server keeps the config it started with (#116).
  for (const s of servers) {
    if (s.status === 'running' && s.template_changed) {
      items.push({
        key: `template-changed-${s.id}`,
        subject: s.name,
        text: `Template “${s.template_name}” was edited after this server started. Players get the change after a restart.`,
        action: { kind: 'restart', id: s.id },
      })
    }
  }

  // Missing server files block every server on that branch, so say it once per branch.
  const missing = {}
  for (const s of servers) {
    if (!s.server_files_ready) (missing[s.branch] ||= []).push(s.name)
  }
  for (const [branch, names] of Object.entries(missing)) {
    items.push({
      key: `files-missing-${branch}`,
      subject: `${branchLabel(branch)} server files`,
      text: `Not downloaded yet, so ${joinNames(names)} can't start.`,
      action: { kind: 'server-files' },
    })
  }

  // A newer server release on Steam (#177).
  for (const u of updates) {
    if (missing[u.branch]) continue // nothing installed to update; the item above covers it
    const onBranch = servers.filter((s) => s.branch === u.branch).map((s) => s.name)
    let text = `Build ${u.installed_build} → ${u.latest_build} is out.`
    if (u.downloading) text += ' Downloading it now.'
    else if (autoDownload) text += ' It downloads automatically.'
    else if (onBranch.length) text += ` ${joinNames(onBranch)} keep${onBranch.length === 1 ? 's' : ''} the installed build until you update.`
    items.push({
      key: `update-${u.branch}`,
      subject: `New ${u.label || branchLabel(u.branch)} server release`,
      text,
      action: u.downloading ? { kind: 'server-files' } : { kind: 'update', branch: u.branch },
    })
  }

  return items
}

/**
 * SVG geometry for a sparkline over `values`, drawn to one scale inside
 * width × height with `pad` kept clear for the stroke and end dot. Missing
 * readings (null) are left out rather than drawn as zero. Returns null when there
 * are fewer than two readings — one point is not a line.
 */
export function sparkline(values, width = 120, height = 28, pad = 3) {
  const pts = values
    .map((v, i) => [i, v])
    .filter(([, v]) => typeof v === 'number' && Number.isFinite(v))
  if (pts.length < 2) return null
  const first = pts[0][0]
  const last = pts[pts.length - 1][0]
  const nums = pts.map(([, v]) => v)
  const min = Math.min(...nums)
  const max = Math.max(...nums)
  const x = (i) => pad + ((i - first) / (last - first)) * (width - 2 * pad)
  // A flat series sits in the middle instead of on the floor.
  const y = (v) => (max === min ? height / 2 : height - pad - ((v - min) / (max - min)) * (height - 2 * pad))
  const coords = pts.map(([i, v]) => [x(i), y(v)].map((n) => Math.round(n * 10) / 10))
  const line = `M${coords.map((c) => c.join(',')).join('L')}`
  const [lx] = coords[coords.length - 1]
  const [fx] = coords[0]
  return { line, area: `${line}L${lx},${height}L${fx},${height}Z` }
}
