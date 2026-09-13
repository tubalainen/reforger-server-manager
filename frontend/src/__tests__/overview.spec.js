import { describe, expect, it } from 'vitest'

import { attentionItems, joinNames, sparkline } from '../overview'

const server = (over = {}) => ({
  id: 1,
  name: 'conflict-1',
  branch: 'stable',
  status: 'running',
  template_name: 'Conflict Everon',
  template_changed: false,
  server_files_ready: true,
  ...over,
})

describe('attentionItems', () => {
  it('is empty when nothing is waiting', () => {
    expect(attentionItems([server()], [], false)).toEqual([])
  })

  it('asks for a restart when a running server has a stale template (#116)', () => {
    const [item] = attentionItems([server({ template_changed: true })])
    expect(item.subject).toBe('conflict-1')
    expect(item.text).toContain('Conflict Everon')
    expect(item.action).toEqual({ kind: 'restart', id: 1 })
  })

  it('ignores a stale template on a server that is not running', () => {
    // The flag only means something for a live config; a stopped server picks the
    // template up when it next starts.
    expect(attentionItems([server({ status: 'exited', template_changed: true })])).toEqual([])
  })

  it('reports missing server files once per branch, naming every server they block', () => {
    const items = attentionItems([
      server({ id: 1, name: 'a', server_files_ready: false }),
      server({ id: 2, name: 'b', server_files_ready: false }),
      server({ id: 3, name: 'c', branch: 'experimental' }),
    ])
    expect(items).toHaveLength(1)
    expect(items[0].subject).toBe('Stable server files')
    expect(items[0].text).toBe("Not downloaded yet, so a and b can't start.")
    expect(items[0].action).toEqual({ kind: 'server-files' })
  })

  it('offers an update for a new release, naming the servers still on the old build', () => {
    const update = { branch: 'stable', label: 'Stable', installed_build: '100', latest_build: '101' }
    const [item] = attentionItems([server(), server({ id: 2, name: 'combat-ops' })], [update])
    expect(item.subject).toBe('New Stable server release')
    expect(item.text).toBe(
      'Build 100 → 101 is out. conflict-1 and combat-ops keep the installed build until you update.',
    )
    expect(item.action).toEqual({ kind: 'update', branch: 'stable' })
  })

  it('points at the running download instead of offering a second one', () => {
    const update = { branch: 'stable', installed_build: '1', latest_build: '2', downloading: true }
    const [item] = attentionItems([server()], [update])
    expect(item.text).toContain('Downloading it now.')
    expect(item.action).toEqual({ kind: 'server-files' })
  })

  it('says when the manager downloads the update by itself', () => {
    const update = { branch: 'stable', installed_build: '1', latest_build: '2' }
    expect(attentionItems([server()], [update], true)[0].text).toContain('downloads automatically')
  })

  it('does not offer to update files that were never downloaded', () => {
    const update = { branch: 'stable', installed_build: '1', latest_build: '2' }
    const items = attentionItems([server({ server_files_ready: false })], [update])
    expect(items.map((i) => i.key)).toEqual(['files-missing-stable'])
  })
})

describe('joinNames', () => {
  it('reads like a sentence', () => {
    expect(joinNames(['a'])).toBe('a')
    expect(joinNames(['a', 'b'])).toBe('a and b')
    expect(joinNames(['a', 'b', 'c'])).toBe('a, b and c')
  })
})

describe('sparkline', () => {
  it('needs two readings to draw a line', () => {
    expect(sparkline([])).toBeNull()
    expect(sparkline([5])).toBeNull()
    expect(sparkline([null, 5, null])).toBeNull()
  })

  it('spans the width and keeps the extremes inside the padding', () => {
    const g = sparkline([0, 10], 100, 20, 2)
    expect(g.line).toBe('M2,18L98,2')
    expect(g.area).toBe('M2,18L98,2L98,20L2,20Z')
  })

  it('draws a flat series through the middle, not along the floor', () => {
    expect(sparkline([3, 3, 3], 100, 20, 2).line).toBe('M2,10L50,10L98,10')
  })

  it('leaves missing readings out instead of plotting them as zero', () => {
    // A null CPU total means "not sampled yet", which must not dip the line to 0.
    const g = sparkline([4, null, 8], 100, 20, 2)
    expect(g.line).toBe('M2,18L98,2')
  })
})
