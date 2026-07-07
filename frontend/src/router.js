import { createRouter, createWebHistory } from 'vue-router'
import { api } from './api'
import Login from './views/Login.vue'
import Instances from './views/Instances.vue'
import Templates from './views/Templates.vue'
import TemplateWizard from './views/TemplateWizard.vue'
import Downloads from './views/Downloads.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: Login, meta: { public: true } },
    { path: '/', name: 'instances', component: Instances },
    { path: '/templates', name: 'templates', component: Templates },
    { path: '/templates/new', name: 'template-new', component: TemplateWizard },
    { path: '/templates/:id/edit', name: 'template-edit', component: TemplateWizard, props: true },
    { path: '/downloads', name: 'downloads', component: Downloads },
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
  if (await checkAuth()) return true
  return { name: 'login', query: { redirect: to.fullPath } }
})

export default router
