<script setup>
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { setAuthed } from './router'

const route = useRoute()
const router = useRouter()

async function logout() {
  await api('/api/auth/logout', { method: 'POST' })
  setAuthed(false)
  router.push({ name: 'login' })
}
</script>

<template>
  <nav v-if="!route.meta.public" class="navbar navbar-expand bg-body-tertiary border-bottom mb-4">
    <div class="container">
      <router-link class="navbar-brand fw-semibold" to="/">⬢ Reforger Server Manager</router-link>
      <ul class="navbar-nav me-auto">
        <li class="nav-item">
          <router-link class="nav-link" active-class="active" to="/">Instances</router-link>
        </li>
        <li class="nav-item">
          <router-link class="nav-link" active-class="active" to="/templates">Templates</router-link>
        </li>
        <li class="nav-item">
          <router-link class="nav-link" active-class="active" to="/downloads">Downloads</router-link>
        </li>
      </ul>
      <button class="btn btn-outline-secondary btn-sm" @click="logout">Log out</button>
    </div>
  </nav>
  <router-view />
</template>
