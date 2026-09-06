<script setup>
// Saved game backups for one instance (#179): make one, put one back, take one
// off the box, bring one back on. The card is deliberately usable while the
// server runs — making a copy is a read — but restoring is not, and says so.
import { computed, onMounted, ref } from 'vue'
import { api, upload } from '../api'
import { formatBytes } from '../format'

const props = defineProps({
  id: { type: [String, Number], required: true },
  running: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const info = ref(null)
const error = ref('')
const notice = ref('')
const busy = ref('')
const label = ref('')
const confirmRestore = ref(null)
const restoreSwitch = ref(true)
const restoreBackupFirst = ref(true)
const confirmDelete = ref(null)
const fileInput = ref(null)

const fmtBytes = formatBytes
// The backend renders the timestamp in the manager's timezone (#112), so the
// row shows what the server logs show, not what this browser's clock thinks.
const fmtTime = (backup) => backup?.created_display || backup?.created_at || ''

const backups = computed(() => info.value?.backups || [])
const state = computed(() => info.value?.state || { size_bytes: 0, files: 0, paths: [] })
const current = computed(() => info.value?.current || {})

async function load() {
  try {
    info.value = await api(`/api/instances/${props.id}/backups`)
    error.value = ''
  } catch (e) {
    // Say so rather than hiding the card: "no backups" and "the manager could
    // not look" are very different answers to "is my world safe?".
    error.value = e.message
  }
}
defineExpose({ load })

const SOURCE_LABEL = {
  manual: 'Made by hand',
  'template-switch': 'Made before a template change',
  'pre-restore': 'Replaced by a restore',
  upload: 'Uploaded',
}

// An instance outlives its templates, so most of a long-lived shelf is worlds
// from setups the server no longer runs. The badge answers the only question
// that matters when picking one: would this server read it as it stands? (#181)
const FIT = {
  match: { badge: 'matches this server', css: 'text-bg-success' },
  'other-hive': { badge: 'different save (hive id)', css: 'text-bg-warning' },
  'other-scenario': { badge: 'different scenario', css: 'text-bg-warning' },
  unknown: { badge: 'origin unknown', css: 'text-bg-secondary' },
}
const fitOf = (backup) => FIT[backup.fit] || FIT.unknown

// Why it does not fit, in the words of the thing the user actually chose: the
// template. Scenario ids are printed too, because two templates can carry the
// same name in conversation and only the id settles it.
function fitExplained(backup) {
  const now = current.value
  if (!backup) return ''
  if (backup.fit === 'match') {
    return `This backup matches what this server is set to now — scenario ${now.scenario_id}${
      now.hive_id === null || now.hive_id === undefined ? '' : `, hive id ${now.hive_id}`
    }.`
  }
  if (backup.fit === 'unknown') {
    return 'This file does not say which scenario wrote it, so the manager cannot tell whether this server will read it.'
  }
  const wrote = backup.template_name ? `template "${backup.template_name}"` : 'another template'
  if (backup.fit === 'other-hive') {
    return `Same scenario, different save: this world was written by ${wrote} for hive id ${backup.hive_id}, and this server is set to hive id ${now.hive_id}. The engine will not load one into the other.`
  }
  return `This world was written by ${wrote}, scenario ${backup.scenario_id}. This server is now set to ${now.scenario_id}, and a scenario does not read another scenario's world.`
}

function describe(backup) {
  const bits = [SOURCE_LABEL[backup.source] || 'Backup']
  if (backup.template_name) bits.push(`template "${backup.template_name}"`)
  if (backup.server_running) bits.push('server was running')
  return bits.join(' · ')
}

async function create() {
  busy.value = 'create'
  notice.value = ''
  try {
    const made = await api(`/api/instances/${props.id}/backups`, {
      method: 'POST',
      body: { label: label.value },
    })
    label.value = ''
    notice.value =
      `Backed up ${made.files} file(s), ${fmtBytes(made.size_bytes)}.` +
      (made.skipped?.length
        ? ` ${made.skipped.length} file(s) could not be read and are NOT in it.`
        : '') +
      (made.pruned?.length ? ` Oldest backup removed (keeping ${info.value.keep}).` : '')
    await load()
    emit('changed')
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}

function openRestore(backup) {
  // Both boxes start ticked: the switch is what makes the world readable, and
  // the copy is the undo a restore otherwise does not have.
  restoreSwitch.value = !!backup.switch_to
  restoreBackupFirst.value = !!state.value.files
  confirmRestore.value = backup
}

async function restore() {
  const backup = confirmRestore.value
  const switching = restoreSwitch.value && backup.switch_to ? backup.switch_to : null
  busy.value = 'restore'
  try {
    const out = await api(`/api/instances/${props.id}/backups/${backup.id}/restore`, {
      method: 'POST',
      body: {
        template_id: switching ? switching.id : null,
        backup_first: restoreBackupFirst.value,
      },
    })
    notice.value =
      `Restored the backup from ${fmtTime(backup)}. ` +
      `It replaced ${out.replaced.files} file(s) of newer data.` +
      (out.switched_to ? ` This server now runs template "${out.switched_to.template_name}".` : '') +
      (out.safety_backup ? ' The world it replaced is on the shelf as "Replaced by a restore".' : '')
    confirmRestore.value = null
    await load()
    emit('changed')
  } catch (e) {
    error.value = e.message
    confirmRestore.value = null
  } finally {
    busy.value = ''
  }
}

async function remove() {
  const backup = confirmDelete.value
  busy.value = 'delete'
  try {
    await api(`/api/instances/${props.id}/backups/${backup.id}`, { method: 'DELETE' })
    notice.value = `Deleted the backup from ${fmtTime(backup)}.`
    confirmDelete.value = null
    await load()
  } catch (e) {
    error.value = e.message
    confirmDelete.value = null
  } finally {
    busy.value = ''
  }
}

function download(backup) {
  window.location.href = `/api/instances/${props.id}/backups/${backup.id}/download`
}

async function pickFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  busy.value = 'upload'
  notice.value = ''
  try {
    const form = new FormData()
    form.append('file', file)
    const added = await upload(`/api/instances/${props.id}/backups/upload`, form)
    notice.value = `Added "${file.name}" to the shelf (${added.files} file(s)). Restore it below when the server is stopped.`
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
    if (fileInput.value) fileInput.value.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="card mt-3">
    <div class="card-header d-flex justify-content-between align-items-center py-2">
      <span class="fw-semibold small">Saved game backups</span>
      <button class="btn btn-sm btn-outline-secondary" @click="load">Refresh</button>
    </div>
    <div class="card-body">
      <p class="text-secondary small mb-2">
        A copy of this server's world — the save points and everything the scenario and
        its mods wrote beside them. Take one before you change the template or the
        scenario: the new setup writes its own data, and the old world is only still
        there if you kept it.
      </p>

      <div v-if="error" class="alert alert-warning py-2 small">
        {{ error }}
        <div class="text-secondary">Use Refresh to try again.</div>
      </div>
      <div v-if="notice" class="alert alert-success py-2 small">{{ notice }}</div>

      <template v-if="info">
        <div class="border rounded p-2 mb-3">
          <div class="small">
            <span class="fw-semibold">To back up now:</span>
            <span class="badge text-bg-secondary ms-1">
              {{ state.files ? fmtBytes(state.size_bytes) : 'nothing saved yet' }}
            </span>
            <span v-if="state.files" class="text-secondary ms-1">
              {{ state.files }} file(s)
            </span>
          </div>
          <small v-if="state.paths.length" class="d-block text-secondary text-break">
            <code>{{ state.paths.slice(0, 6).join(', ') }}</code>
            <span v-if="state.paths.length > 6"> +{{ state.paths.length - 6 }} more</span>
          </small>
          <small v-else class="d-block text-secondary">
            Nothing has been written yet — a backup appears once the scenario saves
            something. Not every scenario does (Game Master, for one).
          </small>
          <small class="d-block text-secondary mt-1">
            This server is set to
            <code v-if="current.scenario_id">{{ current.scenario_id }}</code>
            <span v-else>no scenario yet</span>
            <span v-if="current.hive_id !== null && current.hive_id !== undefined">
              · hive id {{ current.hive_id }}</span>
            <span v-if="current.template_name"> · template "{{ current.template_name }}"</span>
          </small>

          <div class="row g-2 align-items-end mt-2">
            <div class="col-sm">
              <label class="form-label small mb-0" for="backup-label">
                Label (optional)
              </label>
              <input
                id="backup-label"
                v-model="label"
                class="form-control form-control-sm"
                maxlength="120"
                placeholder="e.g. before switching to Freedom Fighters"
              />
            </div>
            <div class="col-sm-auto">
              <button
                class="btn btn-sm btn-primary"
                :disabled="!state.files || busy === 'create'"
                @click="create"
              >
                {{ busy === 'create' ? 'Backing up…' : 'Back up now' }}
              </button>
            </div>
          </div>
          <small v-if="running" class="d-block text-secondary mt-1">
            The server is running, so a backup is still fine to take — it is only a copy.
            A save point being written at that exact moment can land in it half-finished,
            so stop the server first if this copy has to be perfect.
          </small>
        </div>

        <p v-if="!backups.length" class="text-secondary small mb-2">
          No backups yet.
        </p>
        <div v-else class="table-responsive mb-2">
          <table class="table table-sm align-middle mb-0 small">
            <thead>
              <tr><th>Taken</th><th>What it holds</th><th>Size</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="b in backups" :key="b.id">
                <td class="text-nowrap">
                  {{ fmtTime(b) }}
                  <small v-if="b.label" class="d-block text-secondary">{{ b.label }}</small>
                </td>
                <td>
                  {{ b.files ? `${b.files} file(s)` : '—' }}
                  <span class="badge ms-1" :class="fitOf(b).css">{{ fitOf(b).badge }}</span>
                  <small class="d-block text-secondary">{{ describe(b) }}</small>
                  <small v-if="b.fit !== 'match' && b.switch_to" class="d-block text-secondary">
                    loads under template "{{ b.switch_to.name }}"
                  </small>
                </td>
                <td class="text-nowrap">{{ fmtBytes(b.archive_bytes) }}</td>
                <td class="text-end text-nowrap">
                  <button
                    class="btn btn-sm btn-outline-primary me-1"
                    :disabled="running"
                    :title="running ? 'Stop the server to restore a backup' : ''"
                    @click="openRestore(b)"
                  >Restore</button>
                  <button class="btn btn-sm btn-outline-secondary me-1" @click="download(b)">
                    Download
                  </button>
                  <button class="btn btn-sm btn-outline-danger" @click="confirmDelete = b">
                    Delete
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="d-flex flex-wrap gap-2 align-items-center">
          <label class="btn btn-sm btn-outline-secondary mb-0">
            {{ busy === 'upload' ? 'Uploading…' : 'Upload a backup file' }}
            <input
              ref="fileInput"
              type="file"
              accept=".gz,.tgz,application/gzip,application/x-gzip"
              class="d-none"
              :disabled="busy === 'upload'"
              @change="pickFile"
            />
          </label>
          <small class="text-secondary">
            The newest {{ info.keep }} backups are kept; older ones are removed
            automatically. They live on the host with the instance, so a downloaded copy
            is the one that survives the box.
          </small>
        </div>
      </template>

      <p v-else-if="!error" class="text-secondary small mb-0">Loading…</p>
    </div>

    <!-- Restoring overwrites a world. Say whose, and what goes. -->
    <div v-if="confirmRestore" class="modal d-block" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-body">
            <h2 class="h6">Restore the backup from {{ fmtTime(confirmRestore) }}?</h2>
            <p class="small text-secondary mb-2">
              The saved game data this server has now — {{ state.files }} file(s),
              {{ fmtBytes(state.size_bytes) }} — is deleted and replaced by this backup's
              {{ confirmRestore.files || '?' }} file(s).
            </p>

            <div
              class="alert py-2 small mb-2"
              :class="confirmRestore.fit === 'match' ? 'alert-secondary' : 'alert-warning'"
            >
              {{ fitExplained(confirmRestore) }}
            </div>

            <div v-if="confirmRestore.switch_to" class="form-check small mb-2">
              <input
                id="restore-switch"
                v-model="restoreSwitch"
                class="form-check-input"
                type="checkbox"
              />
              <label class="form-check-label" for="restore-switch">
                Also switch this server to template
                "{{ confirmRestore.switch_to.name }}" — the setup that reads this world
                <small v-if="!confirmRestore.switch_to.exact" class="d-block text-secondary">
                  Closest match: it runs the right scenario, but its hive id
                  ({{ confirmRestore.switch_to.hive_id }}) still differs from this
                  backup's ({{ confirmRestore.hive_id }}).
                </small>
              </label>
            </div>
            <p
              v-else-if="confirmRestore.fit === 'other-scenario' || confirmRestore.fit === 'other-hive'"
              class="small text-secondary mb-2"
            >
              No template on this manager writes to that save, so nothing here will load
              this world. Restore it anyway to keep the files in place, then create or
              import a template with that scenario.
            </p>

            <div v-if="state.files" class="form-check small mb-2">
              <input
                id="restore-backup-first"
                v-model="restoreBackupFirst"
                class="form-check-input"
                type="checkbox"
              />
              <label class="form-check-label" for="restore-backup-first">
                Back up the world being replaced first
                <span class="text-secondary">({{ fmtBytes(state.size_bytes) }})</span>
              </label>
            </div>
            <p v-else class="small text-secondary mb-2">
              There is nothing to replace — this server has written no saved game data yet.
            </p>

            <p class="small text-secondary mb-0">
              Logs, crash reports and the downloaded mods are left alone, and so is the
              server's own identity token.
            </p>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" @click="confirmRestore = null">
              Cancel
            </button>
            <button class="btn btn-primary" :disabled="busy === 'restore'" @click="restore">
              <template v-if="busy === 'restore'">Restoring…</template>
              <template v-else-if="restoreSwitch && confirmRestore.switch_to">
                Switch template &amp; restore
              </template>
              <template v-else>Restore it</template>
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="confirmDelete" class="modal d-block" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-body">
            <h2 class="h6">Delete the backup from {{ fmtTime(confirmDelete) }}?</h2>
            <p class="small text-secondary mb-0">
              The archive is removed from the host. If you have not downloaded it, this
              copy of that world is gone.
            </p>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" @click="confirmDelete = null">
              Cancel
            </button>
            <button class="btn btn-danger" :disabled="busy === 'delete'" @click="remove">
              {{ busy === 'delete' ? 'Deleting…' : 'Delete it' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
