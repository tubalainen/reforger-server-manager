<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const templates = ref([])
const error = ref('')
const loading = ref(true)
let poll = null

async function load() {
  try {
    templates.value = await api('/api/templates')
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function remove(t) {
  if (!confirm(`Delete template "${t.name}"?`)) return
  try {
    await api(`/api/templates/${t.id}`, { method: 'DELETE' })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function download(t) {
  window.location.href = `/api/templates/${t.id}/config.json`
}

const anyLocked = computed(() => templates.value.some((t) => t.locked))

// Stuck locks (a crashed tab, a closed laptop) expire on their own within
// ~90s; this button is for the impatient (#102). A still-open editor simply
// re-acquires on its next heartbeat.
async function clearLocks() {
  if (!confirm('Force-release all template edit locks? Locks from closed or crashed tabs are removed; anyone still editing keeps theirs automatically.')) return
  try {
    await api('/api/templates/locks/clear', { method: 'POST' })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

// Poll like the Instances view does, so another user's changes — and who is
// editing what — show up within a few seconds (#102).
onMounted(async () => {
  await load()
  poll = setInterval(load, 5000)
})
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <div class="container">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">Server templates</h1>
      <div class="btn-group">
        <button v-if="anyLocked" class="btn btn-outline-warning" @click="clearLocks">
          Clear edit locks
        </button>
        <button class="btn btn-primary" @click="router.push({ name: 'template-new' })">
          + New template
        </button>
      </div>
    </div>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>

    <div v-if="loading" class="text-secondary">Loading…</div>

    <div v-else-if="!templates.length" class="card text-center text-secondary py-5">
      <div class="card-body">
        <p class="fs-1 mb-2">📋</p>
        <p class="mb-1">No templates yet.</p>
        <p class="small mb-2">
          Create one to pick a scenario from the Workshop, auto-resolve its mod
          dependencies, tune settings, and export <code>config.json</code>.
        </p>
        <p class="small mb-0">
          Browse scenarios and mods on the
          <a href="https://reforger.armaplatform.com/workshop" target="_blank" rel="noopener">
            Arma Reforger Workshop</a>.
        </p>
      </div>
    </div>

    <div v-else class="list-group">
      <div
        v-for="t in templates"
        :key="t.id"
        class="list-group-item d-flex justify-content-between align-items-center"
      >
        <div>
          <div class="fw-semibold">
            {{ t.name }}
            <span v-if="t.locked" class="badge text-bg-warning ms-1">✎ being edited</span>
          </div>
          <div class="small text-secondary">{{ t.description || 'No description' }}</div>
        </div>
        <div class="btn-group">
          <button class="btn btn-sm btn-outline-secondary" @click="download(t)">config.json</button>
          <button
            class="btn btn-sm btn-outline-primary"
            :disabled="t.locked"
            :title="t.locked ? 'Being edited in another session' : ''"
            @click="router.push({ name: 'template-edit', params: { id: t.id } })"
          >
            Edit
          </button>
          <button class="btn btn-sm btn-outline-danger" :disabled="t.locked" @click="remove(t)">
            Delete
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
