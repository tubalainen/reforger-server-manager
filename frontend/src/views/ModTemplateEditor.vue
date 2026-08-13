<script setup>
// Create / edit one mod template (issue #166).
//
// This is deliberately not the wizard's Mods step: a mod template has no
// scenario, so there is no "scenario mod", no dependency graph to keep whole and
// nothing to demote — every entry is a plain top-level addon, exactly like adding
// a single mod in the wizard (#97), and the array order is the load order (#164)
// that survives into whichever server template loads this list.
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { formatBytes } from '../format'
import {
  extractModIds,
  mergeResolved,
  moveMod,
  normalizeMods,
  reorderMods,
  sortModsByAdded,
  sortModsByName,
} from '../mods'

const props = defineProps({ id: { type: [String, Number], default: null } })
const router = useRouter()
const editing = computed(() => props.id != null)

const name = ref('')
const description = ref('')
const mods = ref([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')

const search = reactive({ q: '', busy: false, results: [], error: '' })
const modAdd = reactive({ busy: false, error: '' })

const fmtSize = (n) => formatBytes(n, { base: 1000, empty: '' })

const searchModIds = computed(() => extractModIds(search.q))
const byModId = (id) => mods.value.find((m) => m.modId === id)
const isEnabled = (id) => mods.value.some((m) => m.modId === id)
const anyLocked = computed(() => mods.value.some((m) => m.version))

onMounted(async () => {
  if (editing.value) {
    try {
      const mt = await api(`/api/mod-templates/${props.id}`)
      name.value = mt.name
      description.value = mt.description
      mods.value = normalizeMods(mt.mods)
      hydrate()
    } catch (e) {
      error.value = e.message
    }
  }
  loading.value = false
})

// Refresh each mod's name and published versions from the Workshop, so the lock
// picker is current and a list saved by id alone gains its names. Best-effort:
// a mod whose page can't be fetched simply keeps what it has.
const hydrating = ref(false)
async function hydrate() {
  const ids = mods.value.map((m) => m.modId)
  if (!ids.length) return
  hydrating.value = true
  const queue = [...ids]
  async function worker() {
    while (queue.length) {
      const id = queue.shift()
      try {
        const asset = await api(`/api/workshop/asset/${id}`)
        const mod = byModId(id) // look it up again: the list may have changed
        if (!mod) continue
        if (asset.versions?.length) mod.versions = asset.versions
        if (!mod.name) mod.name = asset.name
        mod.provides_scenarios = !!asset.scenarios?.length
      } catch {
        /* Workshop unreachable or mod unlisted — keep the row as-is */
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, ids.length) }, worker))
  hydrating.value = false
}

async function runSearch() {
  if (!search.q.trim()) return
  search.busy = true
  search.error = ''
  try {
    search.results = (await api(`/api/workshop/search?q=${encodeURIComponent(search.q)}`)).rows
  } catch (e) {
    search.error = e.message
    search.results = []
  } finally {
    search.busy = false
  }
}

// Add one mod by id. Its dependencies are NOT resolved: a Reforger server
// downloads those itself, so a mod template lists what you chose (#97).
async function mergeFromWorkshop(id) {
  const asset = await api(`/api/workshop/asset/${id}`)
  mods.value = mergeResolved(normalizeMods(mods.value), {
    root: asset.id,
    mods: [{
      modId: asset.id,
      name: asset.name,
      versions: asset.versions || [],
      provides_scenarios: !!asset.scenarios?.length,
      dependencies: [],
    }],
  })
  return byModId(asset.id)
}

async function addByIds(ids) {
  modAdd.busy = true
  modAdd.error = ''
  notice.value = ''
  const dupes = ids.filter(isEnabled)
  const added = []
  const failed = []
  for (const id of ids.filter((i) => !isEnabled(i))) {
    try {
      added.push(await mergeFromWorkshop(id))
    } catch (e) {
      failed.push({ id, msg: e.message })
    }
  }
  modAdd.busy = false
  const notices = []
  if (added.length) {
    notices.push(`Added ${added.length} mod(s): ${added.map((m) => m.name || m.modId).join(', ')}.`)
  }
  if (dupes.length) {
    notices.push(
      `Already on this list, nothing added: ${dupes.map((id) => byModId(id).name || id).join(', ')}.`,
    )
  }
  notice.value = notices.join(' ')
  if (failed.length) {
    // Keep what failed in the field so one typo doesn't cost the rest (#104).
    modAdd.error = `Could not add: ${failed.map((f) => `${f.id} (${f.msg})`).join('; ')}`
    search.q = failed.map((f) => f.id).join(', ')
  } else {
    search.q = ''
  }
}

// One field, both ways: ids / Workshop URLs (several, comma-separated) are added
// straight away; anything else is a Workshop search.
async function submitSearch() {
  const ids = extractModIds(search.q.trim())
  if (ids.length) return addByIds(ids)
  await runSearch()
}

function removeMod(id) {
  mods.value = mods.value.filter((m) => m.modId !== id)
  notice.value = ''
}

function unlockAll() {
  mods.value = mods.value.map((m) => ({ ...m, version: null }))
  notice.value = 'All mods now follow the latest Workshop version.'
}

function sortByName() {
  mods.value = sortModsByName(mods.value)
  notice.value = 'Mods sorted by name.'
}

function sortByAdded() {
  mods.value = sortModsByAdded(mods.value)
  notice.value = 'Mods sorted in the order they were added.'
}

function moveOne(id, dir) {
  mods.value = moveMod(mods.value, id, dir)
}

// Drag and drop, as on the wizard's mod list (#164): native HTML5 events, with
// the ↑ ↓ buttons kept as the keyboard and touch path.
const drag = reactive({ from: -1, over: -1 })

function onDragStart(i, event) {
  drag.from = i
  drag.over = i
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(i)) // Firefox needs this
  }
}
function onDragOver(i, event) {
  if (drag.from < 0) return
  event.preventDefault()
  drag.over = i
}
function onDrop(i) {
  if (drag.from >= 0) mods.value = reorderMods(mods.value, drag.from, i)
  drag.from = -1
  drag.over = -1
}
function onDragEnd() {
  drag.from = -1
  drag.over = -1
}

function lockOptions(m) {
  const opts = [...(m.versions || [])]
  if (m.version && !opts.includes(m.version)) opts.unshift(m.version)
  return opts
}

const canSave = computed(() => !!name.value.trim() && !saving.value)

async function save() {
  saving.value = true
  error.value = ''
  const body = { name: name.value.trim(), description: description.value, mods: mods.value }
  try {
    if (editing.value) {
      await api(`/api/mod-templates/${props.id}`, { method: 'PUT', body })
    } else {
      await api('/api/mod-templates', { method: 'POST', body })
    }
    router.push({ name: 'mod-templates' })
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="container" style="max-width: 60rem">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">{{ editing ? 'Edit mod template' : 'New mod template' }}</h1>
      <button class="btn btn-outline-secondary" @click="router.push({ name: 'mod-templates' })">
        Back
      </button>
    </div>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>
    <div v-if="loading" class="text-secondary">Loading…</div>

    <template v-else>
      <div class="card mb-3">
        <div class="card-body">
          <div class="row g-3">
            <div class="col-md-5">
              <label class="form-label">Name</label>
              <input v-model="name" class="form-control" placeholder="e.g. Milsim pack" />
            </div>
            <div class="col-md-7">
              <label class="form-label">Description <span class="text-secondary small">(optional)</span></label>
              <input
                v-model="description"
                class="form-control"
                placeholder="What this set is for"
              />
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-body">
          <!-- Add mods: one field takes free text OR mod ids / Workshop URLs -->
          <div class="input-group mb-2">
            <input
              v-model="search.q"
              class="form-control"
              placeholder="Search the Workshop, or paste mod ids / Workshop URLs (comma-separated)…"
              @keyup.enter="submitSearch"
            />
            <button
              class="btn btn-primary"
              :disabled="search.busy || modAdd.busy || !search.q.trim()"
              @click="submitSearch"
            >
              {{
                search.busy || modAdd.busy
                  ? 'Working…'
                  : searchModIds.length > 1
                    ? `Add ${searchModIds.length} mods`
                    : searchModIds.length
                      ? 'Add'
                      : 'Search'
              }}
            </button>
          </div>

          <div v-if="search.error" class="alert alert-warning py-2 small mb-2">{{ search.error }}</div>
          <div
            v-if="search.results.length"
            class="list-group mb-2"
            style="max-height: 15rem; overflow-y: auto"
          >
            <div
              v-for="row in search.results"
              :key="row.id"
              class="list-group-item d-flex justify-content-between align-items-center py-2"
            >
              <div class="me-2 text-truncate">
                <div class="fw-semibold text-truncate">{{ row.name }}</div>
                <small class="text-secondary">v{{ row.version }} · {{ fmtSize(row.size) }}</small>
              </div>
              <button
                class="btn btn-sm btn-outline-primary flex-shrink-0"
                :disabled="modAdd.busy || isEnabled(row.id)"
                @click="addByIds([row.id])"
              >{{ isEnabled(row.id) ? 'Added' : 'Add' }}</button>
            </div>
          </div>
          <div v-if="modAdd.busy" class="text-secondary small mb-2">
            <span class="spinner-border spinner-border-sm me-1"></span>Adding mod…
          </div>
          <div v-if="modAdd.error" class="alert alert-info py-2 small mb-2">{{ modAdd.error }}</div>
          <div v-if="notice" class="alert alert-secondary py-2 small mb-2">{{ notice }}</div>

          <div class="d-flex justify-content-between align-items-center mt-3 mb-2">
            <h2 class="h6 mb-0">
              Mods on this list ({{ mods.length }})
              <small v-if="hydrating" class="text-secondary fw-normal ms-1">
                <span class="spinner-border spinner-border-sm me-1" style="width: .75rem; height: .75rem"></span>
                fetching version history…
              </small>
            </h2>
            <div class="btn-group btn-group-sm">
              <button
                class="btn btn-outline-secondary"
                :disabled="mods.length < 2"
                title="Sort the mods alphabetically"
                @click="sortByName"
              >Sort A–Z</button>
              <button
                class="btn btn-outline-secondary"
                :disabled="mods.length < 2"
                title="Sort the mods in the order they were added"
                @click="sortByAdded"
              >Sort as added</button>
              <button
                class="btn btn-outline-secondary"
                :disabled="!anyLocked"
                title="Clear every version lock so all mods follow the latest Workshop release"
                @click="unlockAll"
              >Unlock all</button>
            </div>
          </div>

          <div v-if="!mods.length" class="text-secondary small">
            No mods on this list yet — add some above.
          </div>

          <div v-else class="text-secondary small mb-1">
            This order is carried over when the list is loaded into a server template.
            Drag a row (or use ↑ ↓) to change it.
          </div>

          <ul v-if="mods.length" class="list-group mb-2">
            <li
              v-for="(m, i) in mods"
              :key="m.modId"
              class="list-group-item d-flex justify-content-between align-items-center py-2"
              :class="{
                'opacity-50': drag.from === i,
                'border-primary border-2': drag.over === i && drag.from !== i && drag.from >= 0,
              }"
              draggable="true"
              @dragstart="onDragStart(i, $event)"
              @dragover="onDragOver(i, $event)"
              @drop="onDrop(i)"
              @dragend="onDragEnd"
            >
              <span
                class="text-secondary me-2 flex-shrink-0 text-nowrap"
                style="cursor: grab"
                title="Drag to reorder"
              >
                <span class="font-monospace small">{{ i + 1 }}</span> ⠿
              </span>
              <div class="me-2 text-truncate">
                <span class="fw-semibold">{{ m.name || m.modId }}</span>
                <span
                  v-if="m.provides_scenarios"
                  class="badge text-bg-info ms-1"
                  title="This mod publishes its own scenario(s). A server runs the scenario picked in the server template — loading this list adds the mod for its content."
                >+ scenario</span>
                <small class="text-secondary d-block">{{ m.modId }}</small>
              </div>
              <select
                v-model="m.version"
                class="form-select form-select-sm w-auto ms-auto me-2 flex-shrink-0"
                :title="m.version ? 'Locked to v' + m.version : 'Follows the latest Workshop release'"
              >
                <option :value="null">latest</option>
                <option v-for="v in lockOptions(m)" :key="v" :value="v">🔒 v{{ v }}</option>
              </select>
              <div class="btn-group btn-group-sm flex-shrink-0">
                <button
                  class="btn btn-outline-secondary"
                  :disabled="i === 0"
                  title="Move up"
                  @click="moveOne(m.modId, -1)"
                >↑</button>
                <button
                  class="btn btn-outline-secondary"
                  :disabled="i === mods.length - 1"
                  title="Move down"
                  @click="moveOne(m.modId, 1)"
                >↓</button>
                <button class="btn btn-outline-danger" title="Remove" @click="removeMod(m.modId)">✕</button>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <div class="d-flex justify-content-end gap-2 my-3">
        <button class="btn btn-outline-secondary" @click="router.push({ name: 'mod-templates' })">
          Cancel
        </button>
        <button class="btn btn-primary" :disabled="!canSave" @click="save">
          {{ saving ? 'Saving…' : editing ? 'Save changes' : 'Create mod template' }}
        </button>
      </div>
    </template>
  </div>
</template>
