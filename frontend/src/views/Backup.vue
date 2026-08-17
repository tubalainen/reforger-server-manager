<script setup>
// Backup & restore of server templates and mod templates (#173).
//
// Export is one click — the whole shelf, one file. Import is a conversation:
// the file is previewed against what is already here, template by template,
// and nothing is written until the user has said what to do with each name
// that already exists. The diff lines come from the backend so they read
// exactly like the change log's.
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const summary = ref(null)
const error = ref('')

// The parsed file, its preview, and the decision per template name.
const file = ref(null)
const preview = ref(null)
const decisions = ref({ server_templates: {}, mod_templates: {} })
const expanded = ref({}) // "kind:name" -> show every change line
const busy = ref(false)
const result = ref(null)

const CHANGES_SHOWN = 4

async function loadSummary() {
  try {
    summary.value = await api('/api/backup/summary')
  } catch (e) {
    error.value = e.message
  }
}

function downloadBackup() {
  // A plain GET with the session cookie, served as an attachment — same as the
  // per-template config.json download.
  window.location.href = '/api/backup/export'
}

const KINDS = [
  { key: 'server_templates', label: 'Server templates', one: 'server template' },
  { key: 'mod_templates', label: 'Mod templates', one: 'mod template' },
]

function itemsOf(kind) {
  return preview.value?.[kind] || []
}

const hasPreview = computed(() => !!preview.value)

// What Apply would do, counted the way the backend will report it back.
const plan = computed(() => {
  const counts = { create: 0, overwrite: 0, copy: 0, skip: 0 }
  for (const { key } of KINDS) {
    for (const item of itemsOf(key)) counts[decisions.value[key][item.name] || 'skip'] += 1
  }
  return counts
})

const nothingToDo = computed(
  () => plan.value.create + plan.value.overwrite + plan.value.copy === 0,
)

function resetImport() {
  preview.value = null
  result.value = null
  decisions.value = { server_templates: {}, mod_templates: {} }
  expanded.value = {}
}

async function chooseFile(event) {
  const picked = event.target.files?.[0]
  event.target.value = '' // let the same file be picked again after a mistake
  if (!picked) return
  resetImport()
  error.value = ''
  busy.value = true
  try {
    let parsed
    try {
      parsed = JSON.parse(await picked.text())
    } catch {
      throw new Error('That file is not valid JSON.')
    }
    const res = await api('/api/backup/preview', { method: 'POST', body: parsed })
    file.value = parsed
    preview.value = res
    // Seed each row with what the backend suggests: import what is new, and
    // never overwrite anything without being told to.
    for (const { key } of KINDS) {
      decisions.value[key] = Object.fromEntries(
        res[key].map((item) => [item.name, item.suggested_action]),
      )
    }
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

// The two ways a whole file is normally used: put back everything it holds, or
// only fill in what is missing. Both leave every choice editable afterwards.
function chooseForAll(mode) {
  for (const { key } of KINDS) {
    for (const item of itemsOf(key)) {
      if (item.status === 'new') decisions.value[key][item.name] = 'create'
      else if (item.status === 'differs') {
        decisions.value[key][item.name] = mode === 'restore' ? 'overwrite' : 'skip'
      } else decisions.value[key][item.name] = 'skip'
    }
  }
}

async function applyImport() {
  busy.value = true
  error.value = ''
  try {
    result.value = await api('/api/backup/import', {
      method: 'POST',
      body: { backup: file.value, decisions: decisions.value },
    })
    preview.value = null
    await loadSummary()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const statusBadge = {
  new: { cls: 'text-bg-success', label: 'New' },
  differs: { cls: 'text-bg-warning', label: 'Differs' },
  identical: { cls: 'text-bg-secondary', label: 'Already here' },
}

// The choices a row offers depend on what it is: there is nothing to decide
// about a template that is already identical.
function optionsFor(status) {
  if (status === 'new') {
    return [['create', 'Import it'], ['skip', "Don't import"]]
  }
  if (status === 'differs') {
    return [
      ['skip', 'Keep what I have'],
      ['overwrite', 'Overwrite with the file'],
      ['copy', 'Import as a copy'],
    ]
  }
  return [['skip', 'Nothing to do']]
}

function toggle(kind, name) {
  const key = `${kind}:${name}`
  expanded.value[key] = !expanded.value[key]
}

function shownChanges(kind, item) {
  return expanded.value[`${kind}:${item.name}`]
    ? item.changes
    : item.changes.slice(0, CHANGES_SHOWN)
}

onMounted(loadSummary)
</script>

<template>
  <div class="container">
    <h1 class="h3 mb-3">Backup &amp; restore</h1>

    <p class="text-secondary small">
      A backup holds your <strong>server templates</strong> and
      <strong>mod templates</strong> — everything you built, in one file you can keep
      somewhere safe, move to another machine, or restore after a rebuild. Server
      instances are not part of it: a running server is a container, a port lease and a
      folder of saved games on <em>this</em> host, and none of that travels in a file.
    </p>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>

    <!-- ---------------------------------------------------------------- Export -->
    <div class="card mb-4">
      <div class="card-body">
        <h2 class="h5 card-title">Export</h2>
        <p class="mb-2 small text-secondary" v-if="summary">
          One file with
          <strong>{{ summary.server_templates }}</strong>
          server template{{ summary.server_templates === 1 ? '' : 's' }} and
          <strong>{{ summary.mod_templates }}</strong>
          mod template{{ summary.mod_templates === 1 ? '' : 's' }} — with their scenario,
          mods and load order, every setting, the engine launch parameters and any custom
          config keys you added by hand.
        </p>
        <div class="alert alert-warning py-2 small">
          <strong>Treat the file as a secret.</strong> It contains the server, admin and
          RCON passwords in clear text — that is what makes it a restore rather than a
          sketch. Keep it where you would keep the passwords themselves.
        </div>
        <button
          class="btn btn-primary"
          :disabled="!summary || (!summary.server_templates && !summary.mod_templates)"
          @click="downloadBackup"
        >
          Download backup
        </button>
        <span
          v-if="summary && !summary.server_templates && !summary.mod_templates"
          class="ms-2 small text-secondary"
        >Nothing to back up yet.</span>
      </div>
    </div>

    <!-- ---------------------------------------------------------------- Import -->
    <div class="card">
      <div class="card-body">
        <h2 class="h5 card-title">Import</h2>
        <p class="small text-secondary">
          Pick a backup file and you'll see what it would change, template by template,
          before anything is written. Names that already exist here are never overwritten
          unless you say so.
        </p>

        <label class="btn btn-outline-primary mb-0" :class="{ disabled: busy }">
          {{ busy && !hasPreview ? 'Reading…' : 'Choose a backup file…' }}
          <input type="file" accept=".json,application/json" class="d-none" @change="chooseFile" />
        </label>

        <!-- The result of the last import -->
        <div v-if="result" class="alert alert-success mt-3 mb-0 py-2 small">
          <div class="fw-semibold">
            Imported: {{ result.counts.created }} created,
            {{ result.counts.overwritten }} overwritten,
            {{ result.counts.skipped }} left alone.
          </div>
          <ul class="mb-0 mt-1 ps-3">
            <li v-for="item in [...result.server_templates, ...result.mod_templates]"
                :key="item.name + item.action">
              {{ item.name }} — {{ item.action }}
            </li>
          </ul>
        </div>

        <!-- The preview: one row per template in the file -->
        <div v-if="hasPreview" class="mt-3">
          <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
            <div class="small text-secondary">
              <span v-if="preview.exported_at">Exported {{ preview.exported_at.slice(0, 10) }}</span>
              <span v-if="preview.app_version"> by manager v{{ preview.app_version }}</span>
            </div>
            <div class="btn-group btn-group-sm">
              <button
                class="btn btn-outline-secondary"
                title="Put back everything in the file, replacing templates of the same name"
                @click="chooseForAll('restore')"
              >Restore everything</button>
              <button
                class="btn btn-outline-secondary"
                title="Add only the templates this manager doesn't have; keep everything you already have"
                @click="chooseForAll('missing')"
              >Only what's missing</button>
            </div>
          </div>

          <template v-for="kind in KINDS" :key="kind.key">
            <div v-if="itemsOf(kind.key).length" class="mb-3">
              <div class="fw-semibold small text-secondary mb-1">{{ kind.label }}</div>
              <div class="list-group">
                <div v-for="item in itemsOf(kind.key)" :key="item.name" class="list-group-item">
                  <div class="d-flex flex-wrap justify-content-between align-items-start gap-2">
                    <div class="me-2">
                      <div class="fw-semibold">
                        {{ item.name }}
                        <span class="badge ms-1" :class="statusBadge[item.status].cls">
                          {{ statusBadge[item.status].label }}
                        </span>
                      </div>
                      <div class="small text-secondary">
                        <span v-if="item.scenario_name">{{ item.scenario_name }} · </span>
                        {{ item.mod_count }} mod{{ item.mod_count === 1 ? '' : 's' }}
                      </div>
                    </div>
                    <select
                      v-model="decisions[kind.key][item.name]"
                      class="form-select form-select-sm w-auto"
                      :disabled="item.status === 'identical'"
                    >
                      <option v-for="[value, label] in optionsFor(item.status)" :key="value" :value="value">
                        {{ label }}
                      </option>
                    </select>
                  </div>

                  <!-- Exactly what differs, in the change log's words -->
                  <div v-if="item.changes.length" class="mt-2 small">
                    <ul class="mb-0 ps-3 text-secondary">
                      <li v-for="(change, i) in shownChanges(kind.key, item)" :key="i">{{ change }}</li>
                    </ul>
                    <button
                      v-if="item.changes.length > CHANGES_SHOWN"
                      class="btn btn-link btn-sm p-0"
                      @click="toggle(kind.key, item.name)"
                    >
                      {{ expanded[`${kind.key}:${item.name}`]
                        ? 'Show less'
                        : `Show all ${item.changes.length} differences` }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <div class="d-flex flex-wrap align-items-center gap-2">
            <button class="btn btn-primary" :disabled="busy || nothingToDo" @click="applyImport">
              {{ busy ? 'Importing…' : 'Apply' }}
            </button>
            <button class="btn btn-outline-secondary" :disabled="busy" @click="resetImport">
              Cancel
            </button>
            <span class="small text-secondary">
              <template v-if="nothingToDo">Nothing selected — every template would be left as it is.</template>
              <template v-else>
                {{ plan.create }} to import, {{ plan.overwrite }} to overwrite,
                {{ plan.copy }} as a copy, {{ plan.skip }} left alone.
              </template>
            </span>
          </div>
          <p class="small text-secondary mt-2 mb-0">
            An import is all or nothing: if any part of it can't be applied, none of it is.
            Overwriting a template keeps it in place, so servers built from it follow the
            restored settings the next time they start.
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
