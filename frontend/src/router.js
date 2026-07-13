import { createRouter, createWebHistory } from 'vue-router'
import { api } from './api'
import Login from './views/Login.vue'
import Instances from './views/Instances.vue'
import InstanceDetail from './views/InstanceDetail.vue'
import Templates from './views/Templates.vue'
import TemplateWizard from './views/TemplateWizard.vue'
import Guide from './views/Guide.vue'

const router = createRouter({
  history: createWebHistory(),
  // Scroll to a hashed section (e.g. the Server files block on the Instances
  // page), else to the top on navigation.
  scrollBehavior(to, from, savedPosition) {
    if (to.hash) return { el: to.hash, behavior: 'smooth' }
    if (savedPosition) return savedPosition
    return { top: 0 }
  },
  routes: [
    { path: '/login', name: 'login', component: Login, meta: { public: true } },
    // Templates is the starting point of the workflow, so it is the landing page
    { path: '/', name: 'templates', component: Templates },
    { path: '/templates/new', name: 'template-new', component: TemplateWizard },
    { path: '/templates/:id/edit', name: 'template-edit', component: TemplateWizard, props: true },
    { path: '/instances', name: 'instances', component: Instances },
    { path: '/instances/:id', name: 'instance-detail', component: InstanceDetail, props: true },
    { path: '/guide', name: 'guide', component: Guide },
    // Downloads moved onto the Instances page; keep the old path working.
    { path: '/downloads', redirect: '/instances' },
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
