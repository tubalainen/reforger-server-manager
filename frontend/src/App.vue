<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { setAuthed } from './router'
import { serverStatus } from './status'

const route = useRoute()
const router = useRouter()
const version = ref(null)

async function logout() {
  await api('/api/auth/logout', { method: 'POST' })
  setAuthed(false)
  router.push({ name: 'login' })
}

// --- Top-banner server status bar (#117) ---
// A compact live view of every instance so you see at a glance which servers are
// up and how busy they are from anywhere in the app. Per-server chips while there
// are a few; collapses to an aggregate once there are many so the navbar still fits.
const summary = ref(null)
const servers = computed(() => summary.value?.servers || [])
const CHIP_LIMIT = 4

async function loadSummary() {
  if (route.meta.public) return // no session on the login page
  try {
    summary.value = await api('/api/instances/summary')
  } catch {
    /* transient; keep the last snapshot */
  }
}

// A filled dot in the server's status colour (green online, amber loading, etc.).
function dotClass(s) {
  return serverStatus(s.status, s.server_state).cls.replace('text-bg-', 'bg-')
}
function chipTitle(s) {
  const label = serverStatus(s.status, s.server_state).long
  const players = s.status === 'running' && s.server_state !== 'starting'
    ? ` · ${s.players ?? '—'} players` : ''
  return `${s.name}: ${label}${players}`
}

let summaryPoll = null
onMounted(async () => {
  try {
    version.value = await api('/api/version')
  } catch {
    /* ignore */
  }
  loadSummary()
  summaryPoll = setInterval(loadSummary, 8000)
})
onUnmounted(() => clearInterval(summaryPoll))
</script>

<template>
  <nav v-if="!route.meta.public" class="navbar navbar-expand bg-body-tertiary border-bottom mb-4">
    <div class="container">
      <router-link class="navbar-brand fw-semibold" to="/">⬢ Reforger Server Manager</router-link>
      <ul class="navbar-nav me-auto">
        <li class="nav-item">
          <router-link class="nav-link" active-class="active" exact-active-class="active" to="/">Server Templates</router-link>
        </li>
        <li class="nav-item">
          <router-link class="nav-link" active-class="active" to="/instances">Server Instances</router-link>
        </li>
        <li class="nav-item">
          <router-link class="nav-link" active-class="active" to="/guide">User Guide</router-link>
        </li>
      </ul>
      <!-- Live server status bar (#117); hidden on narrow screens, where the
           Instances page carries its own fuller bar. -->
      <div
        v-if="summary && summary.total"
        class="d-none d-lg-flex align-items-center gap-3 me-3 small"
      >
        <template v-if="servers.length <= CHIP_LIMIT">
          <router-link
            v-for="s in servers"
            :key="s.id"
            :to="{ name: 'instance-detail', params: { id: s.id } }"
            class="d-inline-flex align-items-center gap-1 text-decoration-none text-body"
            :title="chipTitle(s)"
          >
            <span class="d-inline-block rounded-circle" :class="dotClass(s)" style="width: .6rem; height: .6rem"></span>
            <span class="text-truncate" style="max-width: 9rem">{{ s.name }}</span>
            <span
              v-if="s.status === 'running' && s.server_state !== 'starting'"
              class="text-secondary"
            >{{ s.players ?? '—' }}👤</span>
          </router-link>
        </template>
        <router-link
          v-else
          :to="{ name: 'instances' }"
          class="d-inline-flex align-items-center gap-2 text-decoration-none text-body"
          title="Server instances"
        >
          <span class="fw-semibold">{{ summary.running }}</span>
          <span class="text-secondary">/ {{ summary.total }} online</span>
          <span class="text-secondary">· {{ summary.players_total }}👤</span>
        </router-link>
      </div>
      <a
        v-if="version"
        :href="version.repo_url"
        target="_blank"
        rel="noopener"
        class="navbar-text small text-secondary me-3 text-decoration-none"
        :title="'Open ' + version.name + ' on GitHub'"
      >v{{ version.version }} ↗</a>
      <button
        v-if="!version || version.auth_enabled"
        class="btn btn-outline-secondary btn-sm"
        @click="logout"
      >Log out</button>
    </div>
  </nav>
  <router-view />
</template>
