<script setup>
// Create / edit one mod template (issue #166).
//
// This is deliberately not the wizard's Mods step: a mod template has no
// scenario, so there is no "scenario mod", no dependency graph to keep whole and
// nothing to demote — every entry is a plain top-level addon, exactly like adding
// a single mod in the wizard (#97). The list keeps the order mods were added in;
// it is not rearranged here (#216) — load order is the server template's job.
// Each mod can carry a comment (#216), which stays on the list: loading the list
// into a server template does not copy it.
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import ModIdLink from '../components/ModIdLink.vue'
import SaveCommentDialog from '../components/SaveCommentDialog.vue'
import StageBadge from '../components/StageBadge.vue'
import { formatBytes } from '../format'
import { extractModIds, mergeResolved, normalizeMods } from '../mods'

const COMMENT_MAX = 200 // backend MOD_COMMENT_MAX

const props = defineProps({ id: { type: [String, Number], default: null } })
const router = useRouter()
const editing = computed(() => props.id != null)

const name = ref('')
const description = ref('')
const mods = ref([])
// Comments by modId, kept beside the list rather than on each row: the shared
// mod helpers (normalizeMods, mergeResolved) rebuild rows and know nothing of them.
const comments = reactive({})
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
// Release stage (#223): a Prod list opens read-only, a Dev save asks for a comment.
const stage = ref('')
const prodVersion = ref(null)
const isProd = computed(() => stage.value === 'prod')
const askComment = ref(false)

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
      stage.value = mt.stage || ''
      prodVersion.value = mt.prod_version ?? null
      mods.value = normalizeMods(mt.mods)
      for (const m of mt.mods) if (m.comment) comments[m.modId] = m.comment
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
  delete comments[id]
  notice.value = ''
}

function unlockAll() {
  mods.value = mods.value.map((m) => ({ ...m, version: null }))
  notice.value = 'All mods now follow the latest Workshop version.'
}

function lockOptions(m) {
  const opts = [...(m.versions || [])]
  if (m.version && !opts.includes(m.version)) opts.unshift(m.version)
  return opts
}

const canSave = computed(() => !!name.value.trim() && !saving.value && !isProd.value)

function requestSave() {
  if (stage.value === 'dev') askComment.value = true
  else save()
}

const copyingToTest = ref(false)
async function copyToTest() {
  copyingToTest.value = true
  error.value = ''
  try {
    const created = await api(`/api/mod-templates/${props.id}/copy`, { method: 'POST' })
    router.push({ name: 'mod-template-edit', params: { id: created.id } })
  } catch (e) {
    error.value = e.message
  } finally {
    copyingToTest.value = false
  }
}

async function save(comment = '') {
  askComment.value = false
  saving.value = true
  error.value = ''
  const body = {
    name: name.value.trim(),
    description: description.value,
    mods: mods.value.map((m) => ({ ...m, comment: (comments[m.modId] || '').trim() })),
    comment,
  }
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
      <h1 class="h3 mb-0">
        {{ isProd ? 'View mod list' : editing ? 'Edit mod list' : 'New mod list' }}
        <StageBadge :stage="stage" :version="prodVersion" class="fs-6 align-middle ms-1" />
      </h1>
      <button class="btn btn-outline-secondary" @click="router.push({ name: 'mod-templates' })">
        Back
      </button>
    </div>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>
    <div v-if="loading" class="text-secondary">Loading…</div>

    <div
      v-if="isProd"
      class="alert alert-success py-2 d-flex flex-wrap align-items-center justify-content-between gap-2"
    >
      <span>
        This mod list is in Prod, so it is locked. To change it, copy it to Test, take
        the copy through Dev and release it as the next version.
      </span>
      <button class="btn btn-sm btn-success" :disabled="copyingToTest" @click="copyToTest">
        {{ copyingToTest ? 'Copying…' : 'Copy to Test' }}
      </button>
    </div>

    <fieldset v-if="!loading" :disabled="isProd">
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
            The comment is your own note — it stays on this list and is not copied
            into a server template.
          </div>

          <ul v-if="mods.length" class="list-group mb-2">
            <li
              v-for="m in mods"
              :key="m.modId"
              class="list-group-item py-2"
            >
              <div class="row g-2 align-items-center">
                <div class="col-12 col-md-4 text-truncate">
                  <span class="fw-semibold">{{ m.name || m.modId }}</span>
                  <span
                    v-if="m.provides_scenarios"
                    class="badge text-bg-info ms-1"
                    title="This mod publishes its own scenario(s). A server runs the scenario picked in the server template — loading this list adds the mod for its content."
                  >+ scenario</span>
                  <small class="text-secondary d-block"><ModIdLink :mod-id="m.modId" :name="m.name" /></small>
                </div>
                <div class="col">
                  <input
                    v-model="comments[m.modId]"
                    class="form-control form-control-sm"
                    :maxlength="COMMENT_MAX"
                    placeholder="Comment (optional)"
                    :aria-label="`Comment on ${m.name || m.modId}`"
                  />
                </div>
                <div class="col-auto d-flex align-items-center">
                  <select
                    v-model="m.version"
                    class="form-select form-select-sm me-2"
                    style="width: 8rem"
                    :title="m.version ? 'Locked to v' + m.version : 'Follows the latest Workshop release'"
                  >
                    <option :value="null">latest</option>
                    <option v-for="v in lockOptions(m)" :key="v" :value="v">🔒 v{{ v }}</option>
                  </select>
                  <button
                    class="btn btn-sm btn-outline-danger"
                    title="Remove"
                    :aria-label="`Remove ${m.name || m.modId}`"
                    @click="removeMod(m.modId)"
                  >✕</button>
                </div>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <div class="d-flex justify-content-end gap-2 my-3">
        <button class="btn btn-outline-secondary" @click="router.push({ name: 'mod-templates' })">
          Cancel
        </button>
        <button v-if="!isProd" class="btn btn-primary" :disabled="!canSave" @click="requestSave">
          {{ saving ? 'Saving…' : editing ? 'Save changes' : 'Create mod list' }}
        </button>
      </div>
    </fieldset>

    <SaveCommentDialog v-if="askComment" :busy="saving" @save="save" @cancel="askComment = false" />
  </div>
</template>
