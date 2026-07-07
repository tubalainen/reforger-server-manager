<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { setAuthed } from '../router'

const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await api('/api/auth/login', {
      method: 'POST',
      body: { username: username.value, password: password.value },
    })
    setAuthed(true)
    router.push(route.query.redirect || '/')
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="d-flex align-items-center justify-content-center" style="min-height: 100vh">
    <div class="card shadow" style="width: 22rem">
      <div class="card-body p-4">
        <h1 class="h4 mb-1 text-center">⬢ Reforger Server Manager</h1>
        <p class="text-secondary text-center small mb-4">Sign in to manage your servers</p>
        <form @submit.prevent="submit">
          <div class="mb-3">
            <label class="form-label" for="username">Username</label>
            <input id="username" v-model="username" class="form-control" autocomplete="username" required />
          </div>
          <div class="mb-3">
            <label class="form-label" for="password">Password</label>
            <input id="password" v-model="password" type="password" class="form-control" autocomplete="current-password" required />
          </div>
          <div v-if="error" class="alert alert-danger py-2 small">{{ error }}</div>
          <button class="btn btn-primary w-100" :disabled="busy">
            {{ busy ? 'Signing in…' : 'Sign in' }}
          </button>
        </form>
      </div>
    </div>
  </div>
</template>
