<script setup>
// The login's password, changed from the GUI (#204). Team admins have only this
// GUI — the .env file that holds the first password lives on a machine they have
// no shell on — so this page is the only way they can change it.
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { setAuthed } from '../router'

const router = useRouter()
const info = ref(null)
const error = ref('')

const current = ref('')
const next = ref('')
const repeat = ref('')
const busy = ref(false)
const changed = ref(false)

async function load() {
  try {
    info.value = await api('/api/auth/password')
  } catch (e) {
    error.value = e.message
  }
}

const minLength = computed(() => info.value?.min_length || 12)
const changedAt = computed(() =>
  info.value?.changed_at ? new Date(info.value.changed_at).toLocaleString() : '',
)
const problem = computed(() => {
  if (!next.value) return ''
  if (next.value.length < minLength.value) return `Use at least ${minLength.value} characters.`
  if (repeat.value && repeat.value !== next.value) return 'The two new passwords differ.'
  return ''
})
const ready = computed(
  () => current.value && next.value && repeat.value === next.value && !problem.value,
)

async function change() {
  error.value = ''
  changed.value = false
  busy.value = true
  try {
    await api('/api/auth/password', {
      method: 'POST',
      body: { current_password: current.value, new_password: next.value },
    })
    current.value = next.value = repeat.value = ''
    changed.value = true
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function logoutEverywhere() {
  if (!confirm('Log out every session of this login, this one included?')) return
  try {
    await api('/api/auth/logout-all', { method: 'POST' })
  } finally {
    setAuthed(false)
    router.push({ name: 'login' })
  }
}

onMounted(load)
</script>

<template>
  <div class="container" style="max-width: 44rem">
    <h1 class="h3 mb-3">Account</h1>
    <div v-if="error && !info" class="alert alert-danger">{{ error }}</div>

    <div v-if="info && !info.auth_enabled" class="alert alert-secondary">
      The built-in login is off: your reverse proxy signs people in, so there is no
      password to change here.
    </div>

    <template v-else-if="info">
      <div class="card mb-3">
        <div class="card-body">
          <h2 class="h5">Password</h2>
          <p class="text-secondary small mb-3">
            For the login <strong>{{ info.username }}</strong>.
            <template v-if="info.source === 'gui'">
              Set here on {{ changedAt }}.
            </template>
            <template v-else>
              Still the one this install was set up with.
            </template>
            A new password takes effect at once, and everyone else signed in with the
            old one is logged out; you stay signed in. If it is ever forgotten,
            whoever manages this machine can reset it to the original.
          </p>

          <form autocomplete="on" @submit.prevent="change">
            <input type="text" name="username" :value="info.username" autocomplete="username"
                   class="d-none" readonly />
            <div class="mb-3">
              <label class="form-label" for="pw-current">Current password</label>
              <input id="pw-current" v-model="current" type="password" class="form-control"
                     autocomplete="current-password" required />
            </div>
            <div class="mb-3">
              <label class="form-label" for="pw-new">New password</label>
              <input id="pw-new" v-model="next" type="password" class="form-control"
                     autocomplete="new-password" :minlength="minLength" required />
              <div class="form-text">At least {{ minLength }} characters.</div>
            </div>
            <div class="mb-3">
              <label class="form-label" for="pw-repeat">New password again</label>
              <input id="pw-repeat" v-model="repeat" type="password" class="form-control"
                     autocomplete="new-password" required />
            </div>
            <div v-if="problem" class="alert alert-warning py-2 small">{{ problem }}</div>
            <div v-if="error" class="alert alert-danger py-2 small">{{ error }}</div>
            <div v-if="changed" class="alert alert-success py-2 small">
              Password changed. Every other session was logged out.
            </div>
            <button class="btn btn-primary" :disabled="busy || !ready">
              {{ busy ? 'Changing…' : 'Change password' }}
            </button>
          </form>
        </div>
      </div>

      <div class="card mb-3">
        <div class="card-body">
          <h2 class="h5">Sessions</h2>
          <p class="text-secondary small mb-3">
            Think someone else has a session — a shared computer, a lost laptop? Log out
            everywhere; everyone, you included, has to sign in again.
          </p>
          <button type="button" class="btn btn-outline-danger" @click="logoutEverywhere">
            Log out everywhere
          </button>
        </div>
      </div>
    </template>
  </div>
</template>
