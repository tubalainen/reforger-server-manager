import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { HELP, isUnder, LEGACY_REDIRECTS, NAV, SUBNAV } from '../nav'

const Stub = { render: () => null }

// The real route table imports every view; the redirects only need somewhere to
// land, so each new path gets a stub.
function makeRouter() {
  const targets = [
    '/servers', '/servers/:id',
    '/library/templates', '/library/templates/new', '/library/templates/:id/edit',
    '/library/mod-lists', '/library/mod-lists/new', '/library/mod-lists/:id/edit',
    '/library/mods',
    '/system/server-files', '/system/network', '/system/export',
    '/help',
  ]
  return createRouter({
    history: createMemoryHistory(),
    routes: [...targets.map((path) => ({ path, component: Stub })), ...LEGACY_REDIRECTS],
  })
}

async function land(url) {
  const router = makeRouter()
  await router.push(url)
  const { path, hash, query } = router.currentRoute.value
  return { path, hash, query }
}

describe('legacy URLs (#189)', () => {
  it.each([
    ['/', '/servers'],
    ['/instances', '/servers'],
    ['/instances/7', '/servers/7'],
    ['/templates', '/library/templates'],
    ['/templates/new', '/library/templates/new'],
    ['/templates/3/edit', '/library/templates/3/edit'],
    ['/mod-templates', '/library/mod-lists'],
    ['/mod-templates/new', '/library/mod-lists/new'],
    ['/mod-templates/5/edit', '/library/mod-lists/5/edit'],
    // mod templates became mod lists (#189), after 0.56.0 had already moved them here
    ['/library/mod-templates', '/library/mod-lists'],
    ['/library/mod-templates/new', '/library/mod-lists/new'],
    ['/library/mod-templates/5/edit', '/library/mod-lists/5/edit'],
    ['/mods', '/library/mods'],
    ['/backup', '/system/export'],
    ['/downloads', '/system/server-files'],
    ['/guide', '/help'],
  ])('%s lands on %s', async (from, to) => {
    expect((await land(from)).path).toBe(to)
  })

  it('sends the old Server files anchor to its own page, without the anchor', async () => {
    // Server files used to be a section at the bottom of the Instances page, and the
    // Guide and the instance page both linked to it as /instances#server-files.
    expect(await land('/instances#server-files')).toMatchObject({
      path: '/system/server-files',
      hash: '',
    })
  })

  it("keeps the Guide's chapter anchors", async () => {
    expect(await land('/guide#backup')).toMatchObject({ path: '/help', hash: '#backup' })
  })

  it('keeps the query string', async () => {
    expect((await land('/templates/new?import=1')).query).toEqual({ import: '1' })
  })
})

describe('isUnder', () => {
  it('owns its own path and everything below it', () => {
    expect(isUnder('/servers', '/servers')).toBe(true)
    expect(isUnder('/servers/3', '/servers')).toBe(true)
    expect(isUnder('/library/templates/2/edit', '/library/templates')).toBe(true)
  })

  it('does not claim a sibling that merely shares a prefix', () => {
    // "/library/mod-lists" starts with "/library/mod" but is not under "/library/mods".
    expect(isUnder('/library/mod-lists', '/library/mods')).toBe(false)
    expect(isUnder('/serversettings', '/servers')).toBe(false)
  })
})

describe('menu', () => {
  it('has three destinations plus Help', () => {
    expect(NAV.map((n) => n.key)).toEqual(['servers', 'library', 'system'])
    expect(HELP.to).toBe('/help')
  })

  it('points every sub-page link inside its own section', () => {
    for (const [section, items] of Object.entries(SUBNAV)) {
      for (const item of items) expect(isUnder(item.to, `/${section}`)).toBe(true)
    }
  })
})
