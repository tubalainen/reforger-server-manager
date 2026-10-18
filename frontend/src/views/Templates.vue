<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import ChangeLogModal from '../components/ChangeLogModal.vue'
import HelpTip from '../components/HelpTip.vue'
import MenuButton from '../components/MenuButton.vue'
import NewFromProdDialog from '../components/NewFromProdDialog.vue'
import ReleaseDialog from '../components/ReleaseDialog.vue'
import StageBadge from '../components/StageBadge.vue'
import { filterByCategory, groupByStage, isLocked, promoteAction, versionsText } from '../stages'

const router = useRouter()
const templates = ref([])
const error = ref('')
const loading = ref(true)
let poll = null

// --- Change log (#112): a read-only, searchable history per template ---
// The modal itself is shared with the Mod Templates page (#166).
const logTemplate = ref(null)

// Game-mode categories (#223): a filter for the page, a badge per row.
const categories = ref([])
const categoryFilter = ref('all') // 'all' | 'none' | a category id
async function loadCategories() {
  try {
    categories.value = await api('/api/template-categories')
  } catch {
    /* no filter without them; the list itself still works */
  }
}

// Grouped Prod, Dev, Test (#223), so what is golden is never in doubt.
const groups = computed(() => groupByStage(filterByCategory(templates.value, categoryFilter.value)))
const prods = computed(() => templates.value.filter((t) => t.stage === 'prod'))

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
  // Prod is the golden copy: ask a second time, and say what goes with it (#223).
  if (isLocked(t) && !confirm(
    `"${t.name}" is a Prod template. Deleting it also deletes ${versionsText(t.prod_version)} `
    + 'and its change log, for good.\n\nReally delete it?',
  )) return
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

// Duplicate a template (#129): the backend clones it into Test (#223) under a
// free "Copy of …" name, then we jump straight into the copy's editor.
const copying = ref(0)
async function copy(t) {
  copying.value = t.id
  error.value = ''
  try {
    const created = await api(`/api/templates/${t.id}/copy`, { method: 'POST' })
    router.push({ name: 'template-edit', params: { id: created.id } })
  } catch (e) {
    error.value = e.message
  } finally {
    copying.value = 0
  }
}

// Save a template's mods as a new mod list (#201). The backend leaves out the
// scenario's mod and anything only it needed, names the list "<template> mods",
// and we open it in the mod-list editor so it can be renamed or trimmed.
const makingList = ref(0)
async function makeModList(t) {
  makingList.value = t.id
  error.value = ''
  try {
    const created = await api(`/api/templates/${t.id}/mod-list`, { method: 'POST' })
    router.push({ name: 'mod-template-edit', params: { id: created.id } })
  } catch (e) {
    error.value = e.message
  } finally {
    makingList.value = 0
  }
}

// --- Moving up a stage (#223) ---
const promoting = ref(null)
async function promoted() {
  promoting.value = null
  await load()
}

// "Change category…" works in every stage: it is only a label (#223).
const recategorising = ref(null) // { template, categoryId, busy, error }
function openCategory(t) {
  recategorising.value = { template: t, categoryId: t.category_id ?? '', busy: false, error: '' }
}
async function saveCategory() {
  const r = recategorising.value
  r.busy = true
  r.error = ''
  try {
    await api(`/api/templates/${r.template.id}/category`, {
      method: 'PUT',
      body: { category_id: r.categoryId === '' ? null : Number(r.categoryId) },
    })
    recategorising.value = null
    await Promise.all([load(), loadCategories()])
  } catch (e) {
    r.error = e.message
    r.busy = false
  }
}

const newFromProd = ref(false)
function createdFromProd(created) {
  newFromProd.value = false
  router.push({ name: 'template-edit', params: { id: created.id } })
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
  await Promise.all([load(), loadCategories()])
  poll = setInterval(load, 5000)
})
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <div class="container">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">Server templates</h1>
      <div class="d-flex gap-2">
        <button v-if="anyLocked" class="btn btn-outline-warning" @click="clearLocks">
          Clear edit locks
        </button>
        <MenuButton label="+ New template" button-class="btn btn-primary">
          <li>
            <button class="dropdown-item" @click="router.push({ name: 'template-new' })">
              Blank template
            </button>
          </li>
          <li>
            <button class="dropdown-item" @click="newFromProd = true">
              Based on a Prod template…
            </button>
          </li>
        </MenuButton>
      </div>
    </div>

    <div v-if="categories.length && templates.length" class="d-flex align-items-center gap-2 mb-2">
      <label for="category-filter" class="small text-secondary text-nowrap">Category</label>
      <select
        id="category-filter"
        v-model="categoryFilter"
        class="form-select form-select-sm"
        style="width: auto"
      >
        <option value="all">All categories</option>
        <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name }}</option>
        <option value="none">Uncategorised</option>
      </select>
      <router-link :to="{ name: 'categories' }" class="small text-nowrap">Manage…</router-link>
    </div>

    <p class="text-secondary small">
      New templates start in Test, move to Dev for testing, and are released to Prod.
      <HelpTip label="About Test, Dev and Prod">
        <p><strong>Test</strong>: free to change.</p>
        <p>
          <strong>Dev</strong>: being tested on real servers. Still editable, and every
          save asks for a comment for the change log.
        </p>
        <p>
          <strong>Prod</strong>: released and locked, the golden copy. It can be copied
          and deleted, never changed. Each release is a version (v1, v2, …).
        </p>
        <p>
          To change a Prod template, copy it: the copy lands in Test. Take it through
          Dev, then release it as the next version of the original. Servers on the
          original pick it up on their next restart.
        </p>
      </HelpTip>
    </p>

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

    <p v-else-if="!groups.length" class="text-secondary">
      No templates in this category.
    </p>

    <template v-else>
      <section v-for="g in groups" :key="g.stage" class="mb-4">
        <h2 class="h6 d-flex align-items-center gap-2 mb-2">
          <StageBadge :stage="g.stage" />
          <span class="text-secondary fw-normal small">{{ g.hint }}</span>
        </h2>
        <div class="list-group">
          <div
            v-for="t in g.items"
            :key="t.id"
            class="list-group-item d-flex justify-content-between align-items-center gap-2 flex-wrap"
          >
            <div class="me-2">
              <div class="fw-semibold">
                {{ t.name }}
                <StageBadge v-if="isLocked(t)" stage="prod" :version="t.prod_version" class="ms-1" />
                <span
                  v-if="t.category"
                  class="badge rounded-pill border text-body-secondary fw-normal ms-1"
                  title="Category"
                >{{ t.category }}</span>
                <span v-if="t.locked" class="badge text-bg-warning ms-1">✎ being edited</span>
              </div>
              <div class="small text-secondary">
                {{ t.description || 'No description' }}
                <span v-if="t.based_on">
                  · based on {{ t.based_on.name || 'a deleted template' }}<template v-if="t.based_on.version"> v{{ t.based_on.version }}</template>
                </span>
                <span v-if="t.server_count"> · {{ t.server_count }} server(s)</span>
              </div>
            </div>
            <div class="d-flex gap-1">
              <button
                v-if="isLocked(t)"
                class="btn btn-sm btn-outline-primary"
                title="Prod templates are locked: open it read-only"
                @click="router.push({ name: 'template-edit', params: { id: t.id } })"
              >View</button>
              <button
                v-else
                class="btn btn-sm btn-outline-primary"
                :disabled="t.locked"
                :title="t.locked ? 'Being edited in another session' : ''"
                @click="router.push({ name: 'template-edit', params: { id: t.id } })"
              >Edit</button>
              <button
                v-if="promoteAction(t.stage)"
                class="btn btn-sm"
                :class="promoteAction(t.stage).release ? 'btn-outline-success' : 'btn-outline-info'"
                :disabled="t.locked"
                @click="promoting = t"
              >{{ promoteAction(t.stage).label }}</button>
              <MenuButton label="⋯" aria-label="More actions">
                <li><button class="dropdown-item" @click="logTemplate = t">Change log</button></li>
                <li><button class="dropdown-item" @click="openCategory(t)">Change category…</button></li>
                <li>
                  <button
                    class="dropdown-item"
                    title="Download the rendered config.json. It contains the server, admin and RCON passwords in clear text — treat the file as a secret."
                    @click="download(t)"
                  >Download config.json</button>
                </li>
                <li>
                  <button
                    class="dropdown-item"
                    :disabled="copying === t.id"
                    title="Duplicate this template into a new, editable copy in Test"
                    @click="copy(t)"
                  >{{ copying === t.id ? 'Copying…' : 'Copy to Test' }}</button>
                </li>
                <li>
                  <button
                    class="dropdown-item"
                    :disabled="makingList === t.id"
                    title="Save this template's mods as a new mod list. The scenario's mod, and mods only it needs, are left out."
                    @click="makeModList(t)"
                  >{{ makingList === t.id ? 'Saving…' : 'To mod list' }}</button>
                </li>
                <li><hr class="dropdown-divider" /></li>
                <li>
                  <button class="dropdown-item text-danger" :disabled="t.locked" @click="remove(t)">
                    Delete
                  </button>
                </li>
              </MenuButton>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- Change log (#112): read-only history, newest first, searchable -->
    <ChangeLogModal
      v-if="logTemplate"
      :url="`/api/templates/${logTemplate.id}/changelog`"
      :subtitle="logTemplate.name"
      @close="logTemplate = null"
    />
    <ReleaseDialog
      v-if="promoting"
      :item="promoting"
      :items="templates"
      base="/api/templates"
      @close="promoting = null"
      @done="promoted"
    />
    <NewFromProdDialog
      v-if="newFromProd"
      :prods="prods"
      base="/api/templates"
      @close="newFromProd = false"
      @created="createdFromProd"
    />

    <!-- Change category (#223): any stage, Prod included -->
    <div v-if="recategorising" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Category of "{{ recategorising.template.name }}"</h5>
            <button type="button" class="btn-close" aria-label="Close" @click="recategorising = null"></button>
          </div>
          <div class="modal-body">
            <div v-if="recategorising.error" class="alert alert-danger py-2 small">
              {{ recategorising.error }}
            </div>
            <select v-model="recategorising.categoryId" class="form-select" aria-label="Category">
              <option value="">Uncategorised</option>
              <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name }}</option>
            </select>
            <p class="small text-secondary mt-2 mb-0">
              Only a label: servers on this template are not affected, and it works for a
              Prod template too. The change log records it.
            </p>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" @click="recategorising = null">Cancel</button>
            <button class="btn btn-primary" :disabled="recategorising.busy" @click="saveCategory">
              {{ recategorising.busy ? 'Saving…' : 'Save' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
