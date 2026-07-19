<script setup>
import { computed, onMounted, onUnmounted, ref, nextTick } from 'vue'
import { api } from '../api'
import { isErrorLine } from '../log'
import { serverStatus } from '../status'
import { formatBytes, formatUptime, formatTimestamp } from '../format'

const props = defineProps({ id: { type: [String, Number], required: true } })

const inst = ref(null)
const stats = ref(null)
const error = ref('')
const logLines = ref([])
const follow = ref(true)
const logPane = ref(null)
let ws = null
let poll = null

const fmtMem = (bytes) => formatBytes(bytes, { empty: '—' })
const fmtUptime = formatUptime

async function loadStats() {
  try {
    stats.value = await api(`/api/instances/${props.id}/stats`)
  } catch {
    /* transient; keep last */
  }
}

const logFiles = ref([])
async function loadLogFiles() {
  try {
    logFiles.value = await api(`/api/instances/${props.id}/logfiles`)
  } catch {
    /* ignore */
  }
}
const fmtBytes = formatBytes
const fmtTime = formatTimestamp
function downloadLog(path) {
  window.location.href = `/api/instances/${props.id}/logfiles/download?path=${encodeURIComponent(path)}`
}

// "running" means the container is up; the server inside it may still be loading
// mods and the world, and cannot be joined until it says it is online (#76).
// `pending` covers the stop/start/restart the user just asked for, which the old
// server can spend tens of seconds ignoring.
const pending = ref('')
const serverStatusView = computed(() =>
  serverStatus(inst.value?.status, stats.value?.server_state, pending.value),
)

async function loadInstance() {
  try {
    inst.value = await api(`/api/instances/${props.id}`)
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

function clearLog() {
  logLines.value = []
}

function connectLogs() {
  if (ws) ws.close()
  logLines.value = []
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/instances/${props.id}/logs`)
  ws.onmessage = (msg) => {
    const ev = JSON.parse(msg.data)
    logLines.value.push(ev.line)
    if (logLines.value.length > 2000) logLines.value.shift()
    if (follow.value) {
      nextTick(() => {
        if (logPane.value) logPane.value.scrollTop = logPane.value.scrollHeight
      })
    }
  }
}

async function action(verb) {
  pending.value = verb
  try {
    inst.value = await api(`/api/instances/${props.id}/${verb}`, { method: 'POST' })
    // Refresh the live stats at once: otherwise the status keeps showing the old
    // server's "online" for up to a poll interval after a restart (#76).
    await loadStats()
    await loadData()  // stopping unlocks the data controls; a start refills the dirs
    // reconnect logs after start/restart (a new container may exist)
    if (verb !== 'stop') setTimeout(connectLogs, 800)
  } catch (e) {
    error.value = e.message
  } finally {
    pending.value = ''
  }
}

async function setRestartSetting(field) {
  try {
    inst.value = await api(`/api/instances/${props.id}/restart-settings`, {
      method: 'PUT',
      body: { [field]: !inst.value[field] },
    })
  } catch (e) {
    error.value = e.message
  }
}

const newTime = ref('')
async function saveSchedule(times) {
  try {
    inst.value = await api(`/api/instances/${props.id}/schedule`, {
      method: 'PUT',
      body: { times },
    })
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}
function addTime() {
  if (!newTime.value) return
  const times = [...(inst.value.restart_times || []), newTime.value]
  newTime.value = ''
  saveSchedule(times)
}
function removeTime(t) {
  saveSchedule((inst.value.restart_times || []).filter((x) => x !== t))
}

const editingPorts = ref(false)
const portForm = ref({ game_port: null, a2s_port: null, rcon_port: null })
function openPortEditor() {
  portForm.value = {
    game_port: inst.value.game_port,
    a2s_port: inst.value.a2s_port,
    rcon_port: inst.value.rcon_port,
  }
  editingPorts.value = true
}
async function savePorts() {
  try {
    inst.value = await api(`/api/instances/${props.id}/ports`, {
      method: 'PUT',
      body: portForm.value,
    })
    editingPorts.value = false
  } catch (e) {
    error.value = e.message
  }
}

// --- template swap (issue #31) ---
const templates = ref([])
async function loadTemplates() {
  try {
    templates.value = await api('/api/templates')
  } catch {
    /* ignore; swap UI just won't populate */
  }
}
const editingTemplate = ref(false)
const templateForm = ref(null)
function openTemplateEditor() {
  templateForm.value = inst.value.template_id
  editingTemplate.value = true
}
const currentTemplate = computed(() =>
  templates.value.find((t) => t.id === inst.value?.template_id),
)
const selectedTemplate = computed(() =>
  templates.value.find((t) => t.id === templateForm.value),
)
// Warn when the new template writes to a different persistent save (issue #31)
const hiveWarning = computed(() => {
  const cur = currentTemplate.value
  const next = selectedTemplate.value
  if (!cur || !next || next.id === cur.id) return ''
  if (cur.persistence && next.persistence && cur.hive_id !== next.hive_id)
    return `hiveId changes (${cur.hive_id} → ${next.hive_id}): this instance will use a different persistent save.`
  if (cur.persistence && !next.persistence)
    return 'the new template has persistence disabled — existing saved game data will no longer load.'
  if (!cur.persistence && next.persistence)
    return `the new template enables persistence (hiveId ${next.hive_id}) — a new save will be created.`
  return ''
})
async function saveTemplate() {
  try {
    inst.value = await api(`/api/instances/${props.id}/template`, {
      method: 'PUT',
      body: { template_id: templateForm.value },
    })
    editingTemplate.value = false
  } catch (e) {
    error.value = e.message
  }
}

// --- stored data: baked mods, saves, logs (issue #79) ------------------------
// Editing a template's mods and just restarting can leave the server running the
// old content: it reuses the addons it already downloaded and baked. Wiping that
// makes the next start rebuild it. Saves and logs are offered alongside, because
// the same "give me a clean slate" moment usually wants them gone too.
const DATA_KINDS = {
  mods: {
    label: 'Downloaded & baked mods',
    hint: 'The addons this server downloaded for its template. Clear this when you changed the template\'s mods but the server still runs the old ones — the next start downloads and bakes them again.',
    danger: false,
  },
  saves: {
    label: 'Saved game data',
    hint: 'The persistent world your players built. Clearing it starts the scenario from scratch — there is no undo.',
    danger: true,
  },
  logs: {
    label: 'Logs & crash reports',
    hint: 'Past sessions\' console logs and crash dumps. Safe to clear; only history is lost.',
    danger: false,
  },
}

const dataInfo = ref(null)
const dataPicked = ref([])

// Toggled explicitly rather than with v-model on the array: v-model reads the
// array it was last patched with, so two boxes ticked before a re-render both
// build on the same stale copy and only the last one survives.
function pickData(target, on) {
  const next = new Set(dataPicked.value)
  if (on) next.add(target)
  else next.delete(target)
  dataPicked.value = [...next]
}
const dataBusy = ref(false)
const dataNotice = ref('')
const dataError = ref('')
const confirmClear = ref(false)

async function loadData() {
  try {
    dataInfo.value = await api(`/api/instances/${props.id}/data`)
    dataError.value = ''
  } catch (e) {
    // Never swallow this: the card used to be hidden behind `v-if="dataInfo"`, so a
    // failed call made the whole feature vanish with no explanation — indistinguishable
    // from "this version doesn't have it" (#85). Say what went wrong instead.
    dataError.value = e.message
  }
}

const dataItems = computed(() =>
  (dataInfo.value?.items || []).map((i) => ({ ...i, ...DATA_KINDS[i.target] })),
)
const pickedItems = computed(() => dataItems.value.filter((i) => dataPicked.value.includes(i.target)))
const clearingSaves = computed(() => dataPicked.value.includes('saves'))

async function clearData() {
  dataBusy.value = true
  try {
    const res = await api(`/api/instances/${props.id}/data/clear`, {
      method: 'POST',
      body: { targets: dataPicked.value },
    })
    const freed = res.removed.reduce((n, r) => n + r.size_bytes, 0)
    dataNotice.value =
      `Cleared ${res.removed.map((r) => DATA_KINDS[r.target].label.toLowerCase()).join(', ')}` +
      ` (${fmtBytes(freed)} freed).` +
      (dataPicked.value.includes('mods')
        ? ' The next start will download and bake the mods again — expect it to take a while.'
        : '')
    dataPicked.value = []
    confirmClear.value = false
    await loadData()
  } catch (e) {
    error.value = e.message
    confirmClear.value = false
  } finally {
    dataBusy.value = false
  }
}

// --- rename / change branch (issue #27) ---
const editingBasics = ref(false)
const basicsForm = ref({ name: '', branch: 'stable' })
function openBasicsEditor() {
  basicsForm.value = { name: inst.value.name, branch: inst.value.branch }
  editingBasics.value = true
}
async function saveBasics() {
  try {
    inst.value = await api(`/api/instances/${props.id}`, {
      method: 'PUT',
      body: { name: basicsForm.value.name, branch: basicsForm.value.branch },
    })
    editingBasics.value = false
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  await loadInstance()
  await loadStats()
  await loadLogFiles()
  await loadData()
  await loadTemplates()
  connectLogs()
  poll = setInterval(() => {
    loadInstance()
    loadStats()
  }, 5000)
})
onUnmounted(() => {
  if (ws) ws.close()
  clearInterval(poll)
})
</script>

<template>
  <div class="container">
    <router-link to="/instances" class="btn btn-sm btn-outline-secondary mb-3">← Instances</router-link>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>

    <div v-if="inst">
      <div class="d-flex justify-content-between align-items-start mb-2">
        <div>
          <div class="text-secondary small text-uppercase" style="letter-spacing: .04em">Instance</div>
          <h1 class="h3 mb-0 d-flex align-items-center flex-wrap gap-2">
            <span>{{ inst.name }}</span>
            <span class="badge fs-6 align-middle" :class="serverStatusView.cls">
              <span
                v-if="serverStatusView.starting"
                class="spinner-border spinner-border-sm me-1 align-[-0.125em]"
                role="status"
                aria-hidden="true"
                style="width: .7em; height: .7em; border-width: .15em"
              ></span>
              {{ serverStatusView.label }}
            </span>
          </h1>
        </div>
        <div class="btn-group">
          <button
            v-if="inst.status !== 'running'"
            class="btn btn-success"
            :disabled="!inst.server_files_ready"
            @click="action('start')"
          >Start</button>
          <button v-else class="btn btn-outline-secondary" @click="action('stop')">Stop</button>
          <button class="btn btn-outline-primary" @click="action('restart')">Restart</button>
        </div>
      </div>

      <div v-if="!inst.server_files_ready" class="alert alert-warning py-2">
        The {{ inst.branch }} server files are not downloaded yet —
        <router-link to="/instances#server-files">download them under Server files on the Instances tab</router-link> before starting.
      </div>

      <!-- Live status strip -->
      <div v-if="stats" class="card mb-3">
        <div class="card-body py-2">
          <div class="row text-center g-2 small">
            <div class="col-6 col-md-2">
              <div class="text-secondary">Status</div>
              <div class="fs-6">
                <span class="badge" :class="serverStatusView.cls">{{ serverStatusView.long }}</span>
              </div>
              <div v-if="serverStatusView.note" class="text-secondary" style="font-size: .75rem">
                {{ serverStatusView.note }}
              </div>
            </div>
            <div class="col-6 col-md-2">
              <div class="text-secondary">Uptime</div>
              <div class="fs-5 fw-semibold">{{ inst.status === 'running' ? fmtUptime(stats.uptime_seconds) : '—' }}</div>
            </div>
            <div class="col-6 col-md-2">
              <div class="text-secondary">Players</div>
              <div class="fs-5 fw-semibold">{{ stats.players ?? '—' }}</div>
            </div>
            <div class="col-6 col-md-2">
              <div class="text-secondary">Server FPS</div>
              <div class="fs-5 fw-semibold">{{ stats.server_fps ?? '—' }}</div>
            </div>
            <div class="col-6 col-md-2">
              <div class="text-secondary">CPU</div>
              <div class="fs-5 fw-semibold">{{ stats.cpu_percent != null ? stats.cpu_percent + '%' : '—' }}</div>
            </div>
            <div class="col-6 col-md-2">
              <div class="text-secondary">Memory</div>
              <div class="fs-5 fw-semibold">{{ fmtMem(stats.mem_bytes) }}</div>
              <!-- The limit and the server's own reading were both being computed
                   and then thrown away (#88). They are worth showing: the limit is
                   what the container may use, and the server's figure is the game
                   engine's own view of its heap. -->
              <div v-if="stats.mem_limit_bytes" class="text-secondary" style="font-size: .75rem">
                of {{ fmtMem(stats.mem_limit_bytes) }}
              </div>
              <div
                v-if="stats.server_mem_kb"
                class="text-secondary"
                style="font-size: .75rem"
                title="Memory the Arma server reports for itself (-logStats)"
              >
                server {{ fmtMem(stats.server_mem_kb * 1024) }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="row g-3 mb-3">
        <div class="col-md-8">
          <div class="card">
            <div class="card-body small">
              <div class="row">
                <div class="col-sm-6"><span class="text-secondary">Template:</span> {{ inst.template_name || '—' }}</div>
                <div class="col-sm-6"><span class="text-secondary">Branch:</span> {{ inst.branch }}</div>
                <div class="col-sm-6"><span class="text-secondary">Game port:</span> {{ inst.game_port }}/udp</div>
                <div class="col-sm-6"><span class="text-secondary">A2S port:</span> {{ inst.a2s_port }}/udp</div>
                <div class="col-sm-6"><span class="text-secondary">RCON port:</span> {{ inst.rcon_port }}/udp</div>
                <div class="col-sm-6"><span class="text-secondary">Desired:</span> {{ inst.desired_state }}</div>
                <div class="col-sm-6">
                  <span class="text-secondary">Connect:</span>
                  <template v-if="stats && stats.connect">
                    {{ stats.connect }}
                    <span
                      v-if="stats.public_address_detected"
                      class="badge text-bg-secondary ms-1"
                      title="Auto-detected from the server log; set PUBLIC_ADDRESS in .env to override"
                    >auto</span>
                  </template>
                  <span v-else class="text-secondary fst-italic" title="Set PUBLIC_ADDRESS in .env, or start the server so its public IP can be detected from the log">set PUBLIC_ADDRESS in .env</span>
                </div>
              </div>

              <div class="mt-2">
                <button
                  v-if="!editingPorts"
                  class="btn btn-sm btn-outline-secondary"
                  :disabled="inst.status === 'running'"
                  :title="inst.status === 'running' ? 'Stop the server to edit ports' : ''"
                  @click="openPortEditor"
                >Edit ports</button>
                <div v-else class="row g-2 align-items-end mt-1">
                  <div class="col-4">
                    <label class="form-label small mb-0">Game</label>
                    <input v-model.number="portForm.game_port" type="number" class="form-control form-control-sm" />
                  </div>
                  <div class="col-4">
                    <label class="form-label small mb-0">A2S</label>
                    <input v-model.number="portForm.a2s_port" type="number" class="form-control form-control-sm" />
                  </div>
                  <div class="col-4">
                    <label class="form-label small mb-0">RCON</label>
                    <input v-model.number="portForm.rcon_port" type="number" class="form-control form-control-sm" />
                  </div>
                  <div class="col-12 d-flex gap-2 mt-2">
                    <button class="btn btn-sm btn-primary" @click="savePorts">Save ports</button>
                    <button class="btn btn-sm btn-outline-secondary" @click="editingPorts = false">Cancel</button>
                  </div>
                </div>
              </div>

              <div class="mt-2">
                <button
                  v-if="!editingTemplate"
                  class="btn btn-sm btn-outline-secondary"
                  :disabled="inst.status === 'running'"
                  :title="inst.status === 'running' ? 'Stop the server to change its template' : ''"
                  @click="openTemplateEditor"
                >Change template</button>
                <div v-else class="mt-1">
                  <label class="form-label small mb-0">Template</label>
                  <select v-model.number="templateForm" class="form-select form-select-sm">
                    <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
                  </select>
                  <div v-if="hiveWarning" class="alert alert-warning py-1 px-2 small mt-2 mb-0">
                    ⚠ {{ hiveWarning }}
                  </div>
                  <div class="d-flex gap-2 mt-2">
                    <button class="btn btn-sm btn-primary" @click="saveTemplate">Save template</button>
                    <button class="btn btn-sm btn-outline-secondary" @click="editingTemplate = false">Cancel</button>
                  </div>
                </div>
              </div>

              <div class="mt-2">
                <button
                  v-if="!editingBasics"
                  class="btn btn-sm btn-outline-secondary"
                  @click="openBasicsEditor"
                >Edit name / branch</button>
                <div v-else class="row g-2 align-items-end mt-1">
                  <div class="col-sm-7">
                    <label class="form-label small mb-0">Name</label>
                    <input
                      v-model.trim="basicsForm.name"
                      class="form-control form-control-sm"
                      autocomplete="off"
                      autocorrect="off"
                      spellcheck="false"
                    />
                  </div>
                  <div class="col-sm-5">
                    <label class="form-label small mb-0">Branch</label>
                    <select
                      v-model="basicsForm.branch"
                      class="form-select form-select-sm"
                      :disabled="inst.status === 'running'"
                      :title="inst.status === 'running' ? 'Stop the server to change its branch' : ''"
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
            </div>
          </div>
        </div>
        <div class="col-md-4">
          <div class="card h-100">
            <div class="card-body">
              <div class="d-flex align-items-center justify-content-between mb-2">
                <div>
                  <div class="fw-semibold small">Auto-restart on crash</div>
                  <div class="text-secondary small">Restart if the server process exits</div>
                </div>
                <div class="form-check form-switch mb-0">
                  <input
                    class="form-check-input"
                    type="checkbox"
                    role="switch"
                    :checked="inst.auto_restart"
                    @change="setRestartSetting('auto_restart')"
                  />
                </div>
              </div>
              <div class="d-flex align-items-center justify-content-between">
                <div>
                  <div class="fw-semibold small">Auto-start on host/Docker restart</div>
                  <div class="text-secondary small">Bring the server back after a reboot</div>
                </div>
                <div class="form-check form-switch mb-0">
                  <input
                    class="form-check-input"
                    type="checkbox"
                    role="switch"
                    :checked="inst.auto_start"
                    @change="setRestartSetting('auto_start')"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Scheduled restarts -->
      <div class="card mb-3">
        <div class="card-header py-2">
          <span class="fw-semibold small">Scheduled restarts</span>
        </div>
        <div class="card-body">
          <p class="text-secondary small mb-2">
            Restart this server automatically at set times each day (server local
            time). Handy for clearing memory leaks or applying mod updates.
          </p>
          <div v-if="inst.restart_times && inst.restart_times.length" class="d-flex flex-wrap gap-2 mb-2">
            <span
              v-for="t in inst.restart_times"
              :key="t"
              class="badge text-bg-secondary d-inline-flex align-items-center gap-1"
            >
              {{ t }}
              <button
                type="button"
                class="btn-close btn-close-white"
                style="font-size: 0.5rem"
                aria-label="Remove"
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
              <label class="form-label small mb-0">Add a daily time</label>
              <input v-model="newTime" type="time" class="form-control form-control-sm" style="max-width: 9rem" />
            </div>
            <div class="col-auto">
              <button class="btn btn-sm btn-outline-primary" :disabled="!newTime" @click="addTime">Add</button>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center py-2">
          <span class="fw-semibold small">Server log</span>
          <div class="d-flex align-items-center gap-3">
            <div class="form-check form-switch mb-0">
              <input id="follow" v-model="follow" class="form-check-input" type="checkbox" role="switch" />
              <label for="follow" class="form-check-label small">Follow</label>
            </div>
            <button
              class="btn btn-sm btn-outline-secondary py-0"
              :disabled="!logLines.length"
              title="Clear the log window (new output keeps streaming)"
              @click="clearLog"
            >Clear</button>
          </div>
        </div>
        <!-- Line by line so errors can be painted red (#108); the font stack and
             spacing aim for a terminal, not a paragraph. -->
        <div
          ref="logPane"
          class="card-body bg-black small mb-0 rounded-bottom log-pane"
          style="height: 55vh; overflow-y: auto"
        >
          <template v-if="logLines.length">
            <div v-for="(l, i) in logLines" :key="i" :class="{ 'log-error': isErrorLine(l) }">{{ l || ' ' }}</div>
          </template>
          <div v-else class="log-muted">// waiting for log output…</div>
        </div>
      </div>

      <!-- Stored data: baked mods, saves, logs (issue #79). Always rendered — a failed
           load shows an error here rather than hiding the whole feature (#85). -->
      <div class="card mt-3">
        <div class="card-header d-flex justify-content-between align-items-center py-2">
          <span class="fw-semibold small">Stored data</span>
          <button class="btn btn-sm btn-outline-secondary" @click="loadData">Refresh</button>
        </div>
        <div class="card-body">
          <p class="text-secondary small mb-2">
            What this server has written to disk. Clearing something makes the server
            rebuild it from the template the next time it starts.
          </p>

          <div v-if="dataError" class="alert alert-warning py-2 small mb-0">
            Could not read what this server has stored: {{ dataError }}
            <div class="text-secondary">Use Refresh to try again.</div>
          </div>

          <template v-else-if="dataInfo">
          <p v-if="dataInfo.host_path" class="text-secondary small">
            None of it lives inside the container image — it is all kept on the host at
            <code class="text-break">{{ dataInfo.host_path }}</code> and mounted in, so it
            survives container rebuilds and manager updates. Back up the save by copying
            <code>profile/</code> from there.
          </p>

          <div v-if="dataNotice" class="alert alert-success py-2 small">{{ dataNotice }}</div>

          <div v-if="inst.status === 'running'" class="alert alert-secondary py-2 small mb-3">
            Stop the server to clear its data — pulling the addons or the save out from
            under a running server would corrupt both.
          </div>

          <div class="list-group list-group-flush mb-3">
            <!-- input + sibling label[for], not a label wrapping the input: the
                 wrapping form makes a click on the box activate the label too. -->
            <div
              v-for="item in dataItems"
              :key="item.target"
              class="list-group-item d-flex gap-3 align-items-start px-0"
              :class="{ 'opacity-50': inst.status === 'running' }"
            >
              <input
                :id="`clear-${item.target}`"
                class="form-check-input mt-1 flex-shrink-0"
                type="checkbox"
                :checked="dataPicked.includes(item.target)"
                :disabled="inst.status === 'running' || !item.files"
                @change="pickData(item.target, $event.target.checked)"
              />
              <label :for="`clear-${item.target}`" class="flex-grow-1">
                <span class="fw-semibold">{{ item.label }}</span>
                <span class="badge text-bg-secondary ms-2">
                  {{ item.files ? fmtBytes(item.size_bytes) : 'empty' }}
                </span>
                <span v-if="item.danger && item.files" class="badge text-bg-danger ms-1">
                  destructive
                </span>
                <small class="d-block text-secondary">{{ item.hint }}</small>
                <small v-if="item.paths.length" class="d-block text-secondary">
                  <code>{{ item.paths.join(', ') }}</code> · {{ item.files }} file(s)
                </small>
              </label>
            </div>
          </div>

          <button
            class="btn btn-outline-danger"
            :disabled="!dataPicked.length || inst.status === 'running' || dataBusy"
            @click="confirmClear = true"
          >
            {{ dataBusy ? 'Clearing…' : 'Clear selected data' }}
          </button>
          </template>

          <p v-else class="text-secondary small mb-0">Loading…</p>
        </div>
      </div>

      <!-- Log & crash files -->
      <div class="card mt-3">
        <div class="card-header d-flex justify-content-between align-items-center py-2">
          <span class="fw-semibold small">Log &amp; crash files</span>
          <button class="btn btn-sm btn-outline-secondary" @click="loadLogFiles">Refresh</button>
        </div>
        <div class="card-body">
          <p v-if="!logFiles.length" class="text-secondary small mb-0">
            No log files yet. The server writes logs and crash reports here once it has run.
          </p>
          <div v-else class="table-responsive">
            <table class="table table-sm table-hover align-middle mb-0 small">
              <thead>
                <tr><th>File</th><th>Size</th><th>Modified</th><th></th></tr>
              </thead>
              <tbody>
                <tr v-for="f in logFiles" :key="f.path">
                  <td class="text-break">{{ f.path }}</td>
                  <td>{{ fmtBytes(f.size) }}</td>
                  <td>{{ fmtTime(f.modified) }}</td>
                  <td class="text-end">
                    <button class="btn btn-sm btn-outline-primary" @click="downloadLog(f.path)">
                      Download
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- Spell out exactly what is about to be deleted (issue #79) -->
    <div v-if="confirmClear" class="modal d-block" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-body">
            <h2 class="h6">Clear stored data for "{{ inst.name }}"?</h2>
            <p class="small text-secondary mb-2">This deletes, permanently:</p>
            <ul class="small">
              <li v-for="item in pickedItems" :key="item.target">
                <strong>{{ item.label }}</strong> — {{ fmtBytes(item.size_bytes) }}
                ({{ item.files }} file(s))
              </li>
            </ul>
            <div v-if="clearingSaves" class="alert alert-danger py-2 small mb-2">
              The saved game data goes with it: this server's persistent world is gone for
              good, and the scenario starts over from scratch. Nothing here can bring it back.
            </div>
            <p class="small text-secondary mb-0">
              The server rebuilds what it needs on the next start — re-downloading and
              re-baking mods can take several minutes.
            </p>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" @click="confirmClear = false">Cancel</button>
            <button class="btn btn-danger" :disabled="dataBusy" @click="clearData">
              {{ dataBusy ? 'Clearing…' : 'Clear it' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* A terminal, not a paragraph (#108): monospace stack (Cascadia ships with
   Windows Terminal, the rest cover macOS/Linux), tight lines, wrap long ones
   at any character like a real console. */
.log-pane {
  font-family: 'Cascadia Mono', Consolas, 'Ubuntu Mono', 'DejaVu Sans Mono', 'Liberation Mono',
    Menlo, monospace;
  font-size: 0.8rem;
  line-height: 1.4;
  color: #d4d4d4;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.log-error {
  color: #ff6e6e;
}
.log-muted {
  color: #8a8a8a;
}
</style>
