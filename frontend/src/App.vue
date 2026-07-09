<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { setAuthed } from './router'

const route = useRoute()
const router = useRouter()
const version = ref(null)

async function logout() {
  await api('/api/auth/logout', { method: 'POST' })
  setAuthed(false)
  router.push({ name: 'login' })
}

onMounted(async () => {
  try {
    version.value = await api('/api/version')
  } catch {
    /* ignore */
  }
})
</script>

<template>
  <nav v-if="!route.meta.public" class="navbar navbar-expand bg-body-tertiary border-bottom mb-4">
    <div class="container">
      <router-link class="navbar-brand fw-semibold" to="/">⬢ Reforger Server Manager</router-link>
      <ul class="navbar-nav me-auto">
        <li class="nav-item">
          <router-link class="nav-link" active-class="active" exact-active-class="active" to="/">Templates</router-link>
        </li>
        <li class="nav-item">
          <router-link class="nav-link" active-class="active" to="/instances">Instances</router-link>
        </li>
      </ul>
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
