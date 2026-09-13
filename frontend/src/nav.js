// The app's navigation (#189): three destinations on the left rail plus Help, and
// the sub-pages inside Library and System. Kept free of Vue so the redirect table
// and the "which item owns this page" rules can be tested on their own.

export const NAV = [
  { key: 'servers', to: '/servers', label: 'Servers', full: 'Your servers' },
  {
    key: 'library',
    to: '/library',
    label: 'Library',
    full: 'Server templates, mod lists and mods',
  },
  {
    key: 'system',
    to: '/system',
    label: 'System',
    full: 'Server files, ports & firewall, export & import',
  },
]

export const HELP = { key: 'help', to: '/help', label: 'Help', full: 'User guide' }

export const SUBNAV = {
  library: [
    { to: '/library/templates', label: 'Server templates' },
    { to: '/library/mod-lists', label: 'Mod lists' },
    { to: '/library/mods', label: 'Mods overview' },
  ],
  system: [
    { to: '/system/server-files', label: 'Server files' },
    { to: '/system/network', label: 'Ports & firewall' },
    { to: '/system/export', label: 'Export & import' },
  ],
}

// A page belongs to a nav item when it is that item's path or anything below it, so
// the template wizard lights up Library › Server templates and a server's own page
// lights up Servers.
export function isUnder(path, to) {
  return path === to || path.startsWith(`${to}/`)
}

// Every URL the app used before #189, sent to where that page lives now. Bookmarks,
// links in old release notes and the Guide's own anchors keep working. vue-router
// carries the query and hash across unless a target sets its own.
export const LEGACY_REDIRECTS = [
  { path: '/', redirect: '/servers' },
  {
    path: '/instances',
    // Server files used to be a section at the bottom of this page.
    redirect: (to) =>
      to.hash === '#server-files' ? { path: '/system/server-files', hash: '' } : '/servers',
  },
  { path: '/instances/:id', redirect: (to) => `/servers/${to.params.id}` },
  { path: '/templates', redirect: '/library/templates' },
  { path: '/templates/new', redirect: '/library/templates/new' },
  { path: '/templates/:id/edit', redirect: (to) => `/library/templates/${to.params.id}/edit` },
  { path: '/mod-templates', redirect: '/library/mod-lists' },
  { path: '/mod-templates/new', redirect: '/library/mod-lists/new' },
  {
    path: '/mod-templates/:id/edit',
    redirect: (to) => `/library/mod-lists/${to.params.id}/edit`,
  },
  // Mod templates were renamed mod lists (#189); their pages moved with the name.
  { path: '/library/mod-templates', redirect: '/library/mod-lists' },
  { path: '/library/mod-templates/new', redirect: '/library/mod-lists/new' },
  {
    path: '/library/mod-templates/:id/edit',
    redirect: (to) => `/library/mod-lists/${to.params.id}/edit`,
  },
  { path: '/mods', redirect: '/library/mods' },
  { path: '/backup', redirect: '/system/export' },
  { path: '/downloads', redirect: '/system/server-files' },
  { path: '/guide', redirect: '/help' },
]
