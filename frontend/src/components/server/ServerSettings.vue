<script setup>
import { computed, ref } from 'vue'
import { api } from '../../api'
import { formatBytes } from '../../format'
import StoredData from '../StoredData.vue'

// Everything about how a server is set up, in one tab (#189): name, game version and
// template; ports; crash/reboot behaviour and daily restarts; clearing mods and
// logs; deleting it. Each group still saves on its own — merging them into one form
// with one Save is the next step of #189.
const props = defineProps({
  inst: { type: Object, required: true },
  connect: { type: String, default: '' },
  addressDetected: { type: Boolean, default: false },
})
const emit = defineEmits(['update', 'changed', 'delete'])

const error = ref('')
const notice = ref('')
const running = computed(() => props.inst.status === 'running')
const fmtBytes = formatBytes

// Every endpoint here answers with the server's fresh view; hand it up so the page
// header and the other tabs see the change at once.
async function put(path, body) {
  error.value = ''
  return api(`/api/instances/${props.inst.id}${path}`, { method: 'PUT', body })
}

// --- rename / change branch (#27) ---------------------------------------------
const editingBasics = ref(false)
const basicsForm = ref({ name: '', branch: 'stable' })
function openBasicsEditor() {
  basicsForm.value = { name: props.inst.name, branch: props.inst.branch }
  editingBasics.value = true
}
async function saveBasics() {
  try {
    emit('update', await put('', { name: basicsForm.value.name, branch: basicsForm.value.branch }))
    editingBasics.value = false
  } catch (e) {
    error.value = e.message
  }
}

// --- template swap (#31) --------------------------------------------------------
const templates = ref([])
const editingTemplate = ref(false)
const templateForm = ref(null)
// Ticked by default whenever there is a world to lose: the save the old scenario
// built is exactly what a template swap orphans (#179).
const backupBeforeSwap = ref(true)
const savedData = ref({ files: 0, size_bytes: 0 })

async function openTemplateEditor() {
  templateForm.value = props.inst.template_id
  editingTemplate.value = true
  try {
    templates.value = await api('/api/templates')
  } catch {
    /* the picker just stays empty */
  }
  try {
    const data = await api(`/api/instances/${props.inst.id}/data`)
    savedData.value = data.items.find((i) => i.target === 'saves') || savedData.value
  } catch {
    /* no backup offer without knowing there is a world */
  }
}
const hasSavedData = computed(() => !!savedData.value.files)
const currentTemplate = computed(() => templates.value.find((t) => t.id === props.inst.template_id))
const selectedTemplate = computed(() => templates.value.find((t) => t.id === templateForm.value))

// Warn when the new template writes to a different persistent save (#31)
const hiveWarning = computed(() => {
  const cur = currentTemplate.value
  const next = selectedTemplate.value
  if (!cur || !next || next.id === cur.id) return ''
  if (cur.persistence && next.persistence && cur.hive_id !== next.hive_id)
    return `hiveId changes (${cur.hive_id} → ${next.hive_id}): this server will use a different persistent save.`
  if (cur.persistence && !next.persistence)
    return 'the new template has persistence disabled — existing saved game data will no longer load.'
  if (!cur.persistence && next.persistence)
    return `the new template enables persistence (hiveId ${next.hive_id}) — a new save will be created.`
  return ''
})

async function saveTemplate() {
  try {
    const res = await put('/template', {
      template_id: templateForm.value,
      backup_first: backupBeforeSwap.value && hasSavedData.value,
    })
    const { backup, ...view } = res
    emit('update', view)
    editingTemplate.value = false
    if (backup) {
      notice.value =
        `Saved game data backed up (${fmtBytes(backup.size_bytes)}) before the change` +
        ' — it is on the Saves tab.'
    }
    emit('changed')
  } catch (e) {
    error.value = e.message
  }
}

// --- ports ------------------------------------------------------------------------
const editingPorts = ref(false)
const portForm = ref({ game_port: null, a2s_port: null, rcon_port: null })
function openPortEditor() {
  portForm.value = {
    game_port: props.inst.game_port,
    a2s_port: props.inst.a2s_port,
    rcon_port: props.inst.rcon_port,
  }
  editingPorts.value = true
}
async function savePorts() {
  try {
    emit('update', await put('/ports', portForm.value))
    editingPorts.value = false
  } catch (e) {
    error.value = e.message
  }
}

// --- crash / reboot behaviour (#26) ---------------------------------------------
async function setRestartSetting(field) {
  try {
    emit('update', await put('/restart-settings', { [field]: !props.inst[field] }))
  } catch (e) {
    error.value = e.message
  }
}

// --- daily restarts ------------------------------------------------------------------
const newTime = ref('')
async function saveSchedule(times) {
  try {
    emit('update', await put('/schedule', { times }))
  } catch (e) {
    error.value = e.message
  }
}
function addTime() {
  if (!newTime.value) return
  const times = [...(props.inst.restart_times || []), newTime.value]
  newTime.value = ''
  saveSchedule(times)
}
function removeTime(t) {
  saveSchedule((props.inst.restart_times || []).filter((x) => x !== t))
}
</script>

<template>
  <div class="d-grid gap-3">
    <div v-if="error" class="alert alert-warning py-2 mb-0">{{ error }}</div>
    <div v-if="notice" class="alert alert-success py-2 mb-0">{{ notice }}</div>

    <!-- General -->
    <div class="card">
      <div class="card-header py-2 fw-semibold small">General</div>
      <div class="card-body d-grid gap-3">
        <div>
          <div class="d-flex flex-wrap align-items-center gap-2">
            <span class="rsm-setting-label text-secondary">Name &amp; game version</span>
            <span>{{ inst.name }} · <span class="text-capitalize">{{ inst.branch }}</span></span>
            <button
              v-if="!editingBasics"
              class="btn btn-sm btn-outline-secondary ms-auto"
              @click="openBasicsEditor"
            >Edit</button>
          </div>
          <div v-if="editingBasics" class="row g-2 align-items-end mt-1">
            <div class="col-sm-7">
              <label class="form-label small mb-0" for="set-name">Name</label>
              <input
                id="set-name"
                v-model.trim="basicsForm.name"
                class="form-control form-control-sm"
                autocomplete="off"
                autocorrect="off"
                spellcheck="false"
              />
            </div>
            <div class="col-sm-5">
              <label class="form-label small mb-0" for="set-branch">Game version</label>
              <select
                id="set-branch"
                v-model="basicsForm.branch"
                class="form-select form-select-sm"
                :disabled="running"
                :title="running ? 'Stop the server to change its branch' : ''"
              >
                <option value="stable">Stable</option>
                <option value="experimental">Experimental</option>
              </select>
            </div>
            <div class="col-12 d-flex gap-2 mt-2">
              <button class="btn btn-sm btn-primary" :disabled="!basicsForm.name" @click="saveBasics">Save</button>
              <button class="btn btn-sm btn-outline-secondary" @click="editingBasics = false">Cancel</button>
            </div>
          </div>
        </div>

        <div class="border-top pt-3">
          <div class="d-flex flex-wrap align-items-center gap-2">
            <span class="rsm-setting-label text-secondary">Template</span>
            <span>{{ inst.template_name || '—' }}</span>
            <button
              v-if="!editingTemplate"
              class="btn btn-sm btn-outline-secondary ms-auto"
              :disabled="running"
              :title="running ? 'Stop the server to change its template' : ''"
              @click="openTemplateEditor"
            >Change template</button>
          </div>
          <div v-if="editingTemplate" class="mt-2">
            <label class="form-label small mb-0" for="set-template">Template</label>
            <select id="set-template" v-model.number="templateForm" class="form-select form-select-sm">
              <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
            </select>
            <div v-if="hiveWarning" class="alert alert-warning py-1 px-2 small mt-2 mb-0">
              ⚠ {{ hiveWarning }}
            </div>
            <div v-if="hasSavedData" class="form-check small mt-2">
              <input
                id="backup-before-swap"
                v-model="backupBeforeSwap"
                class="form-check-input"
                type="checkbox"
              />
              <label class="form-check-label" for="backup-before-swap">
                Back up the saved game data first
                <span class="text-secondary">({{ fmtBytes(savedData.size_bytes) }})</span>
              </label>
            </div>
            <div class="d-flex gap-2 mt-2">
              <button class="btn btn-sm btn-primary" @click="saveTemplate">Save template</button>
              <button class="btn btn-sm btn-outline-secondary" @click="editingTemplate = false">Cancel</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Network -->
    <div class="card">
      <div class="card-header py-2 fw-semibold small">Network</div>
      <div class="card-body d-grid gap-3">
        <div>
          <div class="d-flex flex-wrap align-items-center gap-2">
            <span class="rsm-setting-label text-secondary">Ports (UDP)</span>
            <span class="rsm-num">
              game {{ inst.game_port }} · A2S {{ inst.a2s_port }} · RCON {{ inst.rcon_port }}
            </span>
            <button
              v-if="!editingPorts"
              class="btn btn-sm btn-outline-secondary ms-auto"
              :disabled="running"
              :title="running ? 'Stop the server to edit ports' : ''"
              @click="openPortEditor"
            >Edit ports</button>
          </div>
          <div v-if="editingPorts" class="row g-2 align-items-end mt-1">
            <div class="col-4">
              <label class="form-label small mb-0" for="port-game">Game</label>
              <input id="port-game" v-model.number="portForm.game_port" type="number" class="form-control form-control-sm" />
            </div>
            <div class="col-4">
              <label class="form-label small mb-0" for="port-a2s">A2S</label>
              <input id="port-a2s" v-model.number="portForm.a2s_port" type="number" class="form-control form-control-sm" />
            </div>
            <div class="col-4">
              <label class="form-label small mb-0" for="port-rcon">RCON</label>
              <input id="port-rcon" v-model.number="portForm.rcon_port" type="number" class="form-control form-control-sm" />
            </div>
            <div class="col-12 d-flex gap-2 mt-2">
              <button class="btn btn-sm btn-primary" @click="savePorts">Save ports</button>
              <button class="btn btn-sm btn-outline-secondary" @click="editingPorts = false">Cancel</button>
            </div>
          </div>
        </div>
        <div class="border-top pt-3 d-flex flex-wrap align-items-center gap-2">
          <span class="rsm-setting-label text-secondary">Join address</span>
          <template v-if="connect">
            <code>{{ connect }}</code>
            <span
              v-if="addressDetected"
              class="badge text-bg-secondary"
              title="Auto-detected from the server log; set PUBLIC_ADDRESS in .env to override"
            >auto</span>
          </template>
          <span
            v-else
            class="text-secondary fst-italic"
            title="Set PUBLIC_ADDRESS in .env, or start the server so its public IP can be detected from the log"
          >set PUBLIC_ADDRESS in .env</span>
        </div>
      </div>
    </div>

    <!-- Automation -->
    <div class="card">
      <div class="card-header py-2 fw-semibold small">Automation</div>
      <div class="card-body d-grid gap-3">
        <div class="d-flex align-items-center justify-content-between gap-3">
          <label for="set-auto-restart">
            <span class="d-block">Restart after a crash</span>
            <small class="text-secondary">Brings the server back only if its process exits, not after a planned reboot</small>
          </label>
          <div class="form-check form-switch mb-0">
            <input
              id="set-auto-restart"
              class="form-check-input"
              type="checkbox"
              role="switch"
              :checked="inst.auto_restart"
              @change="setRestartSetting('auto_restart')"
            />
          </div>
        </div>
        <div class="d-flex align-items-center justify-content-between gap-3 border-top pt-3">
          <label for="set-auto-start">
            <span class="d-block">Start after the host or Docker restarts</span>
            <small class="text-secondary">Brings the server back after a reboot</small>
          </label>
          <div class="form-check form-switch mb-0">
            <input
              id="set-auto-start"
              class="form-check-input"
              type="checkbox"
              role="switch"
              :checked="inst.auto_start"
              @change="setRestartSetting('auto_start')"
            />
          </div>
        </div>
        <div class="border-top pt-3">
          <div class="mb-2">
            <span class="d-block">Daily restarts</span>
            <small class="text-secondary">
              Server local time. Handy for clearing memory leaks or applying mod updates.
            </small>
          </div>
          <div v-if="inst.restart_times && inst.restart_times.length" class="d-flex flex-wrap gap-2 mb-2">
            <span
              v-for="t in inst.restart_times"
              :key="t"
              class="badge text-bg-secondary d-inline-flex align-items-center gap-1 rsm-num"
            >
              {{ t }}
              <button
                type="button"
                class="btn-close btn-close-white"
                style="font-size: 0.5rem"
                :aria-label="`Remove ${t}`"
                @click="removeTime(t)"
              ></button>
            </span>
          </div>
          <p v-else class="text-secondary small mb-2">No scheduled restarts.</p>
          <p v-if="inst.next_restart" class="small mb-2">
            <span class="text-secondary">Next restart:</span>
            <span class="fw-semibold">{{ inst.next_restart }}</span>
            <span class="text-secondary">(server time)</span>
          </p>
          <div class="row g-2 align-items-end">
            <div class="col-auto">
              <label class="form-label small mb-0" for="set-new-time">Add a daily time</label>
              <input id="set-new-time" v-model="newTime" type="time" class="form-control form-control-sm" style="max-width: 9rem" />
            </div>
            <div class="col-auto">
              <button class="btn btn-sm btn-outline-primary" :disabled="!newTime" @click="addTime">Add</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Maintenance: the saved game is on the Saves tab -->
    <StoredData
      :id="inst.id"
      :running="running"
      :targets="['mods', 'logs']"
      :server-name="inst.name"
      title="Maintenance"
      @changed="emit('changed')"
    />

    <!-- Delete -->
    <div class="card border-danger-subtle">
      <div class="card-body d-flex flex-wrap align-items-center gap-2">
        <span class="me-auto">
          <span class="d-block">Delete this server</span>
          <small class="text-secondary">
            Removes its container. You choose whether its data on disk goes too.
          </small>
        </span>
        <button class="btn btn-outline-danger" @click="emit('delete')">Delete server…</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rsm-setting-label {
  min-width: 11rem;
}
</style>
