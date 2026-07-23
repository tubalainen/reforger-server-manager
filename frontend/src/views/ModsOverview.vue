<script setup>
// Mods Overview (issue #131): one persistent, template-independent list of every
// mod ever baked into a server template. A tree of mods and their (live-resolved)
// dependencies; each mod highlighted where it is baked & downloaded to a server;
// tick any node(s) to add them to a template in one click; prune/persist to keep
// the list current.
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { allModIds, buildForest, subtreeIds } from '../modtree'
import ModTreeNode from '../components/ModTreeNode.vue'

const mods = ref([])
const tree = ref({ edges: {}, names: {}, types: {}, missing: [], resolved: true })
const templates = ref([])
const loading = ref(true)
const treeLoading = ref(false)
const error = ref('')
const notice = ref('')

const selected = ref(new Set())
const busyPersist = ref(new Set())
const addTarget = ref('')
const adding = ref(false)
const rescanning = ref(false)

const forest = computed(() => buildForest(mods.value, tree.value))
const selectedCount = computed(() => selected.value.size)

async function loadMods() {
  mods.value = (await api('/api/mods')).mods
}

// The dependency tree is scraped live from the Workshop, so it loads separately:
// the list shows instantly and the tree fills in (or stays flat if unreachable).
async function loadTree() {
  treeLoading.value = true
  try {
    tree.value = await api('/api/mods/tree')
  } catch {
    tree.value = { edges: {}, names: {}, missing: [], resolved: false }
  } finally {
    treeLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    ;[templates.value] = [await api('/api/templates')]
    await loadMods()
    loadTree() // fire-and-forget; not awaited so the list paints immediately
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// Ticking a node cascades to its whole dependency subtree: selecting a top-level
// mod selects everything beneath it, and clearing it clears the subtree too (#131).
function toggle(node) {
  const ids = subtreeIds(node)
  const turningOn = !selected.value.has(node.modId)
  const next = new Set(selected.value)
  for (const id of ids) {
    if (turningOn) next.add(id)
    else next.delete(id)
  }
  selected.value = next
}

function selectAll() {
  selected.value = new Set(allModIds(forest.value))
}
function clearSelection() {
  selected.value = new Set()
}

async function setPersist(id, val) {
  notice.value = ''
  const busy = new Set(busyPersist.value)
  busy.add(id)
  busyPersist.value = busy
  try {
    await api(`/api/mods/${id}`, { method: 'PATCH', body: { persist: val } })
    await loadMods()
  } catch (e) {
    error.value = e.message
  } finally {
    const done = new Set(busyPersist.value)
    done.delete(id)
    busyPersist.value = done
  }
}

async function remove(id, name) {
  if (!confirm(`Remove "${name}" from the mods overview?`)) return
  notice.value = ''
  try {
    await api(`/api/mods/${id}`, { method: 'DELETE' })
    const next = new Set(selected.value)
    next.delete(id)
    selected.value = next
    await loadMods()
  } catch (e) {
    error.value = e.message
  }
}

async function rescan() {
  if (!confirm(
    'Clear and rescan mods?\n\nMods no template uses any more are removed (persisted mods are kept), and the list is re-synced to your templates.',
  )) return
  rescanning.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await api('/api/mods/rescan', { method: 'POST' })
    await loadMods()
    loadTree()
    notice.value = `Rescan done — ${res.pruned} removed, ${res.total} mod(s) in the overview.`
  } catch (e) {
    error.value = e.message
  } finally {
    rescanning.value = false
  }
}

async function addToTemplate() {
  if (!addTarget.value || !selectedCount.value) return
  adding.value = true
  error.value = ''
  notice.value = ''
  const tmpl = templates.value.find((t) => t.id === Number(addTarget.value))
  try {
    const res = await api('/api/mods/add-to-template', {
      method: 'POST',
      body: { template_id: Number(addTarget.value), mod_ids: [...selected.value] },
    })
    const parts = []
    if (res.added.length) {
      parts.push(`Added ${res.added.length} mod(s) to "${tmpl?.name}": ${res.added.map((m) => m.name).join(', ')}.`)
    }
    if (res.skipped.length) {
      parts.push(`${res.skipped.length} already on that template.`)
    }
    notice.value = parts.join(' ') || 'Nothing to add.'
    clearSelection()
    await loadMods()
    loadTree()
  } catch (e) {
    error.value = e.message
  } finally {
    adding.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="container">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">Mods overview</h1>
      <button class="btn btn-outline-warning" :disabled="rescanning" @click="rescan">
        {{ rescanning ? 'Rescanning…' : 'Clear & rescan mods' }}
      </button>
    </div>

    <p class="text-secondary small">
      Every mod ever baked into a server template, kept here even after the template is
      gone. Each entry is tagged with its Workshop type — <strong>Scenario</strong>,
      <strong>Terrain</strong> or <strong>Mod</strong> — and its category tags. Tick mods
      at any level and add them to a template in one click. Green badges show where a mod
      is baked &amp; downloaded to a server, with its version. Pin a mod as
      <strong>persist</strong> so a rescan or delete never removes it.
    </p>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>
    <div v-if="notice" class="alert alert-info py-2">{{ notice }}</div>

    <div v-if="loading" class="text-secondary">Loading…</div>

    <div v-else-if="!mods.length" class="card text-center text-secondary py-5">
      <div class="card-body">
        <p class="fs-1 mb-2">🧩</p>
        <p class="mb-1">No mods yet.</p>
        <p class="small mb-0">
          Add mods to a server template and they'll show up here automatically.
        </p>
      </div>
    </div>

    <template v-else>
      <!-- Action bar: select + add ticked mods to a template -->
      <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
        <button class="btn btn-sm btn-outline-secondary" @click="selectAll">Select all</button>
        <button
          class="btn btn-sm btn-outline-secondary"
          :disabled="!selectedCount"
          @click="clearSelection"
        >Clear</button>
        <span class="small text-secondary">{{ selectedCount }} selected</span>

        <div class="ms-auto d-flex align-items-center gap-2">
          <select v-model="addTarget" class="form-select form-select-sm" style="width: auto">
            <option value="">Add selected to template…</option>
            <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
          <button
            class="btn btn-sm btn-primary"
            :disabled="!addTarget || !selectedCount || adding"
            @click="addToTemplate"
          >{{ adding ? 'Adding…' : 'Add' }}</button>
        </div>
      </div>

      <div v-if="treeLoading" class="small text-secondary mb-2">Resolving dependencies…</div>
      <div v-else-if="!tree.resolved" class="small text-secondary mb-2">
        Workshop unreachable — showing mods without their dependency tree.
      </div>

      <div class="card">
        <div class="card-body py-1">
          <ModTreeNode
            v-for="(node, idx) in forest"
            :key="node.modId + ':' + idx"
            :node="node"
            :selected="selected"
            :busy-persist="busyPersist"
            @toggle="toggle"
            @persist="setPersist"
            @remove="remove"
          />
        </div>
      </div>
    </template>
  </div>
</template>
