<script setup>
// Game-mode categories for server templates (#223): Conflict, HQC, Escapist, …
// A category is only a label to sort templates by. The built-in ones are added
// once; after that every category can be renamed or deleted, and deleting one
// leaves its templates Uncategorised.
import { onMounted, ref } from 'vue'
import { api } from '../api'

const categories = ref([])
const loading = ref(true)
const error = ref('')
const newName = ref('')
const adding = ref(false)
const editing = ref(null) // { id, name } while a row is being renamed

async function load() {
  try {
    categories.value = await api('/api/template-categories')
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function add() {
  if (!newName.value.trim()) return
  adding.value = true
  error.value = ''
  try {
    await api('/api/template-categories', { method: 'POST', body: { name: newName.value } })
    newName.value = ''
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    adding.value = false
  }
}

async function rename() {
  const row = editing.value
  if (!row?.name.trim()) return
  error.value = ''
  try {
    await api(`/api/template-categories/${row.id}`, { method: 'PUT', body: { name: row.name } })
    editing.value = null
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function remove(c) {
  const used = c.template_count
    ? `\n\n${c.template_count} template(s) are filed under it; they become Uncategorised.`
    : ''
  if (!confirm(`Delete the category "${c.name}"?${used}`)) return
  error.value = ''
  try {
    await api(`/api/template-categories/${c.id}`, { method: 'DELETE' })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div class="container" style="max-width: 44rem">
    <h1 class="h3 mb-3">Category management</h1>
    <p class="text-secondary small">
      The game modes server templates are filed under. Pick one when you save a
      template, or change it from the template's ⋯ menu; the Server templates page can
      show one category at a time. A category is only a label: it never changes what a
      server runs.
    </p>

    <div v-if="error" class="alert alert-warning py-2 small">{{ error }}</div>

    <form class="input-group mb-3" @submit.prevent="add">
      <input
        v-model="newName"
        class="form-control"
        maxlength="60"
        placeholder="New category, e.g. Training"
        aria-label="New category name"
      />
      <button class="btn btn-primary" :disabled="adding || !newName.trim()">
        {{ adding ? 'Adding…' : 'Add' }}
      </button>
    </form>

    <div v-if="loading" class="text-secondary">Loading…</div>
    <p v-else-if="!categories.length" class="text-secondary">
      No categories. Add one above.
    </p>
    <div v-else class="list-group">
      <div
        v-for="c in categories"
        :key="c.id"
        class="list-group-item d-flex justify-content-between align-items-center gap-2"
      >
        <form
          v-if="editing && editing.id === c.id"
          class="input-group input-group-sm"
          @submit.prevent="rename"
        >
          <input
            v-model="editing.name"
            class="form-control"
            maxlength="60"
            :aria-label="`New name for ${c.name}`"
            @keydown.esc="editing = null"
          />
          <button class="btn btn-primary" :disabled="!editing.name.trim()">Save</button>
          <button type="button" class="btn btn-outline-secondary" @click="editing = null">Cancel</button>
        </form>
        <template v-else>
          <div>
            <span class="fw-semibold">{{ c.name }}</span>
            <span class="small text-secondary ms-2">
              {{ c.template_count ? `${c.template_count} template(s)` : 'not used' }}
            </span>
          </div>
          <div class="d-flex gap-1">
            <button class="btn btn-sm btn-outline-secondary" @click="editing = { id: c.id, name: c.name }">
              Rename
            </button>
            <button class="btn btn-sm btn-outline-danger" @click="remove(c)">Delete</button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
