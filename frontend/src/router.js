import { createRouter, createWebHistory } from 'vue-router'
import { api } from './api'
import { LEGACY_REDIRECTS } from './nav'
import Login from './views/Login.vue'
import Instances from './views/Instances.vue'
import InstanceDetail from './views/InstanceDetail.vue'
import Templates from './views/Templates.vue'
import TemplateWizard from './views/TemplateWizard.vue'
import ModsOverview from './views/ModsOverview.vue'
import ModTemplates from './views/ModTemplates.vue'
import ModTemplateEditor from './views/ModTemplateEditor.vue'
import Backup from './views/Backup.vue'
import Downloads from './views/Downloads.vue'
import Network from './views/Network.vue'
import Guide from './views/Guide.vue'
import SectionLayout from './views/SectionLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  // Scroll to a hashed section (e.g. a chapter of the Guide), else to the top on
  // navigation.
  scrollBehavior(to, from, savedPosition) {
    if (to.hash) return { el: to.hash, behavior: 'smooth' }
    if (savedPosition) return savedPosition
    return { top: 0 }
  },
  // Route names are what the views link by, so they stayed put when the paths
  // moved under Servers, Library and System, and when mod templates became mod
  // lists (#189).
  routes: [
    { path: '/login', name: 'login', component: Login, meta: { public: true } },
    { path: '/servers', name: 'instances', component: Instances },
    // Overview is the bare /servers/:id; the other tabs add a segment (#189).
    { path: '/servers/:id/:tab?', name: 'instance-detail', component: InstanceDetail, props: true },
    {
      path: '/library',
      component: SectionLayout,
      props: { section: 'library' },
      children: [
        { path: '', redirect: '/library/templates' },
        { path: 'templates', name: 'templates', component: Templates },
        { path: 'templates/new', name: 'template-new', component: TemplateWizard },
        {
          path: 'templates/:id/edit',
          name: 'template-edit',
          component: TemplateWizard,
          props: true,
        },
        { path: 'mod-lists', name: 'mod-templates', component: ModTemplates },
        { path: 'mod-lists/new', name: 'mod-template-new', component: ModTemplateEditor },
        {
          path: 'mod-lists/:id/edit',
          name: 'mod-template-edit',
          component: ModTemplateEditor,
          props: true,
        },
        { path: 'mods', name: 'mods', component: ModsOverview },
      ],
    },
    {
      path: '/system',
      component: SectionLayout,
      props: { section: 'system' },
      children: [
        { path: '', redirect: '/system/server-files' },
        { path: 'server-files', name: 'server-files', component: Downloads },
        { path: 'network', name: 'network', component: Network },
        { path: 'export', name: 'backup', component: Backup },
      ],
    },
    { path: '/help', name: 'guide', component: Guide },
    ...LEGACY_REDIRECTS,
    // Anything else — a mistyped or long-gone link — lands on the servers.
    { path: '/:pathMatch(.*)*', redirect: '/servers' },
  ],
})

let authed = null

export async function checkAuth(force = false) {
  if (authed === null || force) {
    try {
      await api('/api/auth/me')
      authed = true
    } catch {
      authed = false
    }
  }
  return authed
}

export function setAuthed(value) {
  authed = value
}

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  if (!(await checkAuth())) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
