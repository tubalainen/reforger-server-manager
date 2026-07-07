import { createRouter, createWebHistory } from 'vue-router'
import { api } from './api'
import Login from './views/Login.vue'
import Instances from './views/Instances.vue'
import InstanceDetail from './views/InstanceDetail.vue'
import Templates from './views/Templates.vue'
import TemplateWizard from './views/TemplateWizard.vue'
import Downloads from './views/Downloads.vue'
import Welcome from './views/Welcome.vue'

const ONBOARDED_KEY = 'rsm_onboarded'

export function markOnboarded() {
  try {
    localStorage.setItem(ONBOARDED_KEY, '1')
  } catch {
    /* ignore */
  }
}

function isOnboarded() {
  try {
    return localStorage.getItem(ONBOARDED_KEY) === '1'
  } catch {
    return true
  }
}

async function isFreshInstall() {
  try {
    const [templates, instances] = await Promise.all([
      api('/api/templates'),
      api('/api/instances'),
    ])
    return templates.length === 0 && instances.length === 0
  } catch {
    return false
  }
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: Login, meta: { public: true } },
    // Templates is the starting point of the workflow, so it is the landing page
    { path: '/', name: 'templates', component: Templates },
    { path: '/templates/new', name: 'template-new', component: TemplateWizard },
    { path: '/templates/:id/edit', name: 'template-edit', component: TemplateWizard, props: true },
    { path: '/instances', name: 'instances', component: Instances },
    { path: '/instances/:id', name: 'instance-detail', component: InstanceDetail, props: true },
    { path: '/downloads', name: 'downloads', component: Downloads },
    { path: '/welcome', name: 'welcome', component: Welcome },
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
  // First login on a fresh install lands on the getting-started guide
  if (to.name === 'templates' && !isOnboarded() && (await isFreshInstall())) {
    return { name: 'welcome' }
  }
  return true
})

export default router
