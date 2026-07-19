<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { formatDateTime } from '../format'

const router = useRouter()
const templates = ref([])
const error = ref('')
const loading = ref(true)
let poll = null

// --- Change log (#112): a read-only, searchable history per template ---
const showLog = ref(false)
const logTemplate = ref(null)
const logEntries = ref([])
const logLoading = ref(false)
const logError = ref('')
const logSearch = ref('')

async function openLog(t) {
  logTemplate.value = t
  showLog.value = true
  logSearch.value = ''
  logError.value = ''
  logLoading.value = true
  logEntries.value = []
  try {
    logEntries.value = await api(`/api/templates/${t.id}/changelog`)
  } catch (e) {
    logError.value = e.message
  } finally {
    logLoading.value = false
  }
}

// Client-side search: the log is already loaded, so filtering is instant.
const filteredLog = computed(() => {
  const q = logSearch.value.trim().toLowerCase()
  if (!q) return logEntries.value
  return logEntries.value.filter((e) => e.summary.toLowerCase().includes(q))
})

// Group consecutive lines from the same save (same timestamp) under one header.
// Entries arrive newest-first with within-event order preserved, so same-stamp
// rows stay contiguous.
const logGroups = computed(() => {
  const groups = []
  for (const e of filteredLog.value) {
    const last = groups[groups.length - 1]
    if (last && last.at === e.changed_at) last.items.push(e)
    else groups.push({ at: e.changed_at, items: [e] })
  }
  return groups
})

const CATEGORY = {
  meta: { label: 'template', cls: 'text-bg-secondary' },
  scenario: { label: 'scenario', cls: 'text-bg-success' },
  mod: { label: 'mod', cls: 'text-bg-primary' },
  setting: { label: 'setting', cls: 'text-bg-info' },
}
const catBadge = (c) => CATEGORY[c] || { label: c, cls: 'text-bg-secondary' }
const fmtTime = formatDateTime

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
          <button class="btn btn-sm btn-outline-secondary" @click="openLog(t)">Change log</button>
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

    <!-- Change log modal (#112): read-only history, newest first, searchable -->
    <div v-if="showLog" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog modal-lg modal-dialog-scrollable">
        <div class="modal-content">
          <div class="modal-header">
            <div>
              <h5 class="modal-title mb-0">Change log</h5>
              <div class="small text-secondary">{{ logTemplate?.name }}</div>
            </div>
            <button type="button" class="btn-close" @click="showLog = false"></button>
          </div>
          <div class="modal-body">
            <input
              v-model="logSearch"
              class="form-control form-control-sm mb-3"
              placeholder="Search the change log…"
              autocomplete="off"
            />
            <div v-if="logError" class="alert alert-warning py-2">{{ logError }}</div>
            <div v-else-if="logLoading" class="text-secondary">Loading…</div>
            <div v-else-if="!logEntries.length" class="text-secondary">
              No changes recorded yet.
            </div>
            <div v-else-if="!logGroups.length" class="text-secondary">
              Nothing matches “{{ logSearch }}”.
            </div>
            <div v-else class="d-flex flex-column gap-3">
              <div v-for="g in logGroups" :key="g.at + g.items[0].id">
                <div class="small text-secondary border-bottom pb-1 mb-2">{{ fmtTime(g.at) }}</div>
                <ul class="list-unstyled mb-0 d-flex flex-column gap-1">
                  <li v-for="e in g.items" :key="e.id" class="d-flex align-items-start gap-2">
                    <span class="badge mt-1" :class="catBadge(e.category).cls" style="min-width: 4.5rem">
                      {{ catBadge(e.category).label }}
                    </span>
                    <span>{{ e.summary }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <span class="small text-secondary me-auto">This log can't be edited or deleted.</span>
            <button class="btn btn-outline-secondary" @click="showLog = false">Close</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
