<script setup>
// Mod templates (issue #166): named, reusable mod lists you keep on a shelf and
// load into the Mods step of any server template. This page is the shelf: create,
// edit, delete, and read the change log of each one.
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import ChangeLogModal from '../components/ChangeLogModal.vue'

const router = useRouter()
const modTemplates = ref([])
const loading = ref(true)
const error = ref('')

// The change-log modal is the very one the Server Templates page uses (#112).
const logTarget = ref(null)

async function load() {
  try {
    modTemplates.value = await api('/api/mod-templates')
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function remove(mt) {
  if (!confirm(
    `Delete the mod template "${mt.name}"?\n\nServer templates you built from it keep their mods — this only removes the list itself, and its change log.`,
  )) return
  try {
    await api(`/api/mod-templates/${mt.id}`, { method: 'DELETE' })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div class="container">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">Mod templates</h1>
      <button class="btn btn-primary" @click="router.push({ name: 'mod-template-new' })">
        + New mod template
      </button>
    </div>

    <p class="text-secondary small">
      A mod template is a saved list of mods — nothing else, no scenario and no server
      settings. Build the set once here, then load it into the <strong>Mods</strong> step of
      any server template, in the order you arranged it. Every change is recorded in the
      mod template's own change log.
    </p>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>

    <div v-if="loading" class="text-secondary">Loading…</div>

    <div v-else-if="!modTemplates.length" class="card text-center text-secondary py-5">
      <div class="card-body">
        <p class="fs-1 mb-2">🧰</p>
        <p class="mb-1">No mod templates yet.</p>
        <p class="small mb-0">
          Create one to keep a set of mods you use over and over — a milsim pack, a
          training set — and load it into any server template in two clicks.
        </p>
      </div>
    </div>

    <div v-else class="list-group">
      <div
        v-for="mt in modTemplates"
        :key="mt.id"
        class="list-group-item d-flex justify-content-between align-items-center"
      >
        <div class="me-2">
          <div class="fw-semibold">
            {{ mt.name }}
            <span class="badge text-bg-secondary ms-1">{{ mt.mod_count }} mods</span>
            <span
              v-if="mt.locked_count"
              class="badge text-bg-light border text-secondary ms-1"
              :title="`${mt.locked_count} mod(s) locked to a specific version`"
            >🔒 {{ mt.locked_count }}</span>
          </div>
          <div class="small text-secondary">{{ mt.description || 'No description' }}</div>
        </div>
        <div class="btn-group">
          <button class="btn btn-sm btn-outline-secondary" @click="logTarget = mt">
            Change log
          </button>
          <button
            class="btn btn-sm btn-outline-primary"
            @click="router.push({ name: 'mod-template-edit', params: { id: mt.id } })"
          >Edit</button>
          <button class="btn btn-sm btn-outline-danger" @click="remove(mt)">Delete</button>
        </div>
      </div>
    </div>

    <ChangeLogModal
      v-if="logTarget"
      :url="`/api/mod-templates/${logTarget.id}/changelog`"
      :subtitle="logTarget.name"
      @close="logTarget = null"
    />
  </div>
</template>
