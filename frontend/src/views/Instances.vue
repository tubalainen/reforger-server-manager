<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import Sparkline from '../components/Sparkline.vue'
import { formatBytes, formatUptime } from '../format'
import { attentionItems, branchLabel } from '../overview'
import { serverStatus } from '../status'

// The Servers overview (#189): host totals, one list of what needs attention, and a
// row per server with its live numbers and quick actions. Everything on it comes
// from /api/instances/summary, so it costs one request per poll however many
// servers there are.

const router = useRouter()

const summary = ref(null)
const servers = computed(() => summary.value?.servers || [])
// New Arma server releases the daily check found (#177).
const updates = ref([])
const autoDownload = ref(false)
const templates = ref([])
const templatesLoaded = ref(false)
const error = ref('')
const showCreate = ref(false)
const orphanedData = ref('')
const create = reactive({
  name: '', template_id: null, branch: 'stable',
  customPorts: false, game_port: null, a2s_port: null, rcon_port: null,
  busy: false, error: '',
})
let poll = null

async function load() {
  try {
    summary.value = await api('/api/instances/summary')
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
  try {
    const auto = await api('/api/serverfiles/auto-update')
    autoDownload.value = auto.auto_download
    updates.value = auto.branches.filter((b) => b.update_available)
  } catch {
    /* keep last */
  }
}

async function loadTemplates() {
  try {
    templates.value = await api('/api/templates')
  } catch (e) {
    create.error = e.message
  } finally {
    templatesLoaded.value = true
  }
}

// --- Totals and their sparklines ----------------------------------------------
const history = computed(() => summary.value?.history || [])
const series = (key) => history.value.map((p) => p[key])
const fmtMem = (n) => formatBytes(n, { empty: '—' })

const totals = computed(() => {
  const s = summary.value
  if (!s) return []
  return [
    {
      key: 'running',
      label: 'Online',
      value: s.running,
      unit: ` / ${s.total} server${s.total === 1 ? '' : 's'}`,
      values: series('running'),
    },
    { key: 'players', label: 'Players', value: s.players_total, unit: '', values: series('players') },
    {
      key: 'cpu',
      label: 'CPU · all servers',
      value: s.cpu_percent != null ? s.cpu_percent : '—',
      unit: s.cpu_percent != null ? '%' : '',
      values: series('cpu_percent'),
      title: "Each server's share of this whole machine, added up",
    },
    {
      key: 'mem',
      label: 'Memory · all servers',
      value: fmtMem(s.mem_bytes),
      unit: '',
      values: series('mem_bytes'),
    },
  ]
})

// --- Needs attention ------------------------------------------------------------
const attention = computed(() => attentionItems(servers.value, updates.value, autoDownload.value))

const ATTENTION_BUTTON = {
  restart: 'Restart',
  update: 'Update server files',
  'server-files': 'Server files',
}

function runAttention(item) {
  const a = item.action
  if (a.kind === 'restart') {
    const s = servers.value.find((x) => x.id === a.id)
    if (s) action(s, 'restart')
  } else if (a.kind === 'update') {
    updateNow(a.branch)
  } else {
    router.push({ name: 'server-files' })
  }
}

// Start the branch's download, then open System › Server files, which picks up the
// running job and streams its progress and log (#177).
async function updateNow(branch) {
  try {
    await api(`/api/serverfiles/${branch}/download`, { method: 'POST' })
  } catch (e) {
    error.value = e.message
    return
  }
  router.push({ name: 'server-files' })
}

// --- Table ----------------------------------------------------------------------
// Stop/start/restart the user asked for but that is still in flight, per server.
const pending = reactive({})

function rowStatus(s) {
  return serverStatus(s.status, s.server_state, pending[s.id])
}
function dotClass(s) {
  return rowStatus(s).cls.replace('text-bg-', 'bg-')
}
const isRunning = (s) => s.status === 'running'

function playersText(s) {
  const max = s.max_players != null ? ` / ${s.max_players}` : ''
  return `${isRunning(s) && s.players != null ? s.players : '—'}${max}`
}
function playersFill(s) {
  if (!isRunning(s) || s.players == null || !s.max_players) return 0
  return Math.min(100, Math.round((s.players / s.max_players) * 100))
}
const fmtFps = (s) => (s.server_fps != null ? Math.round(s.server_fps) : '—')
const fmtCpu = (s) => (s.cpu_percent != null ? `${s.cpu_percent}%` : '—')
const fmtUp = (s) => (isRunning(s) ? formatUptime(s.uptime_seconds) : '—')
// The backend renders "YYYY-MM-DD HH:MM" in the server's local time; the time of day
// is what matters in a table, the full label goes in the tooltip.
const fmtNext = (s) => (s.next_restart ? s.next_restart.slice(11) : '—')
const scenarioOf = (s) => s.scenario_name || s.template_name || '—'

async function action(s, verb) {
  pending[s.id] = verb
  try {
    await api(`/api/instances/${s.id}/${verb}`, { method: 'POST' })
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    delete pending[s.id]
  }
}

// Restarting everything disconnects every player on the host, so it asks first.
const running = computed(() => servers.value.filter(isRunning))
const restartAll = reactive({ open: false, busy: false })
async function confirmRestartAll() {
  restartAll.busy = true
  await Promise.all(running.value.map((s) => action(s, 'restart')))
  restartAll.busy = false
  restartAll.open = false
}

// --- New server -----------------------------------------------------------------
async function openCreate() {
  create.name = ''
  create.branch = 'stable'
  create.error = ''
  await loadTemplates()
  create.template_id = templates.value[0]?.id ?? null
  showCreate.value = true
}

async function submitCreate() {
  if (!create.name.trim() || !create.template_id) return
  create.busy = true
  create.error = ''
  try {
    const body = { name: create.name, template_id: create.template_id, branch: create.branch }
    if (create.customPorts) {
      body.game_port = create.game_port
      body.a2s_port = create.a2s_port
      body.rcon_port = create.rcon_port
    }
    const made = await api('/api/instances', { method: 'POST', body })
    // The id this server was given may have belonged to a deleted one whose data
    // was kept. It has been moved out of the way rather than adopted — say where
    // it went, or its owner would never find it again (#187).
    orphanedData.value = made.orphaned_data || ''
    showCreate.value = false
    await load()
  } catch (e) {
    create.error = e.message
  } finally {
    create.busy = false
  }
}

const fmtBytes = (n) => formatBytes(n, { empty: 'empty' })

// --- Delete ---------------------------------------------------------------------
// The delete dialog. Its container comes off either way; the stored data on disk
// (mods, saves, logs, configs) is left behind unless the user opts to wipe it too.
const del = reactive({ inst: null, data: null, purge: false, busy: false, error: '' })

// What is on disk, only the targets that actually hold something.
const delItems = computed(() => (del.data?.items || []).filter((i) => i.files))
const delTotalBytes = computed(() =>
  (del.data?.items || []).reduce((sum, i) => sum + (i.size_bytes || 0), 0),
)

function remove(inst) {
  del.inst = inst
  del.data = null
  del.purge = false
  del.busy = false
  del.error = ''
  // Show what wiping would take with it; the delete works fine without this.
  api(`/api/instances/${inst.id}/data`)
    .then((d) => { if (del.inst?.id === inst.id) del.data = d })
    .catch(() => {})
}

async function confirmDelete() {
  const inst = del.inst
  if (!inst) return
  del.busy = true
  del.error = ''
  try {
    const q = del.purge ? '?purge_data=true' : ''
    await api(`/api/instances/${inst.id}${q}`, { method: 'DELETE' })
    del.inst = null
    await load()
  } catch (e) {
    del.error = e.message
  } finally {
    del.busy = false
  }
}

const hasTemplates = computed(() => templates.value.length > 0)

onMounted(async () => {
  await Promise.all([load(), loadTemplates()])
  poll = setInterval(load, 5000)
})
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <div class="container">
    <div class="d-flex flex-wrap align-items-center gap-3 mb-3">
      <div class="me-auto">
        <h1 class="h3 mb-0">Servers</h1>
        <div v-if="summary && summary.total" class="text-secondary small">
          {{ summary.total }} server{{ summary.total === 1 ? '' : 's' }} ·
          {{ summary.running }} online · {{ summary.players_total }}
          player{{ summary.players_total === 1 ? '' : 's' }}
        </div>
      </div>
      <button
        v-if="servers.length"
        class="btn btn-outline-secondary"
        :disabled="!running.length"
        :title="running.length ? '' : 'No server is running'"
        @click="restartAll.open = true"
      >Restart all running</button>
      <button class="btn btn-primary" @click="openCreate">New server</button>
    </div>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>

    <div v-if="orphanedData" class="alert alert-info py-2 small d-flex gap-2 align-items-start">
      <span>
        This server was given an id that a deleted server had used, and that server's
        stored data — its world and its backups — was still on disk. It has been moved
        to <code class="text-break">{{ orphanedData }}</code> rather than handed to the
        new server, which starts empty. Nothing was deleted.
      </span>
      <button class="btn-close ms-auto" aria-label="Dismiss" @click="orphanedData = ''"></button>
    </div>

    <!-- Nothing to show yet: point at the one next step. -->
    <div v-if="summary && !servers.length" class="card text-center py-5">
      <div class="card-body">
        <template v-if="templatesLoaded && !hasTemplates">
          <h2 class="h5">Start in the Library</h2>
          <p class="text-secondary mb-3">
            A server runs a template: the scenario, its mods and settings. Build one first.
          </p>
          <router-link class="btn btn-primary" :to="{ name: 'template-new' }">New template</router-link>
        </template>
        <template v-else>
          <h2 class="h5">No servers yet</h2>
          <p class="text-secondary mb-3">
            Create one from a template to run an Arma Reforger server in its own container.
          </p>
          <button class="btn btn-primary" @click="openCreate">New server</button>
        </template>
      </div>
    </div>

    <template v-else-if="summary">
      <!-- Host totals, each with the last hour as a sparkline -->
      <div class="rsm-totals mb-3">
        <div v-for="t in totals" :key="t.key" class="rsm-total" :title="t.title">
          <div class="small text-secondary">{{ t.label }}</div>
          <div class="rsm-total-value">
            {{ t.value }}<span class="rsm-total-unit">{{ t.unit }}</span>
          </div>
          <Sparkline class="text-primary" :values="t.values" :label="`${t.label}, last hour`" />
        </div>
      </div>

      <!-- One list for the whole host, each line with the button that fixes it -->
      <div v-if="attention.length" class="card mb-3">
        <div class="card-header py-2 fw-semibold small">
          Needs attention · {{ attention.length }}
        </div>
        <ul class="list-group list-group-flush">
          <li
            v-for="item in attention"
            :key="item.key"
            class="list-group-item d-flex align-items-start gap-2 py-2"
          >
            <span class="rsm-dot rsm-dot-text rounded-circle bg-warning" aria-hidden="true"></span>
            <div class="d-flex flex-wrap align-items-center gap-2 flex-grow-1">
              <span class="me-auto">
                <strong>{{ item.subject }}</strong>
                <span class="text-secondary"> · {{ item.text }}</span>
              </span>
              <button
                class="btn btn-sm btn-outline-secondary"
                :disabled="item.action.kind === 'restart' && !!pending[item.action.id]"
                @click="runAttention(item)"
              >{{ ATTENTION_BUTTON[item.action.kind] }}</button>
            </div>
          </li>
        </ul>
      </div>

      <!-- A row per server -->
      <div class="card">
        <table class="table rsm-fleet align-middle mb-0">
          <thead>
            <tr class="small">
              <th scope="col">Server</th>
              <th scope="col">Status</th>
              <th scope="col">Players</th>
              <th scope="col" class="text-end">FPS</th>
              <th scope="col" class="text-end">CPU</th>
              <th scope="col" class="text-end">Memory</th>
              <th scope="col" class="text-end">Uptime</th>
              <th scope="col" class="text-end">Next restart</th>
              <th scope="col"><span class="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in servers" :key="s.id">
              <td class="rsm-cell-name">
                <router-link
                  :to="{ name: 'instance-detail', params: { id: s.id } }"
                  class="fw-semibold text-decoration-none"
                >{{ s.name }}</router-link>
                <div class="small text-secondary">{{ scenarioOf(s) }} · {{ branchLabel(s.branch) }}</div>
              </td>
              <td data-label="Status">
                <span class="d-inline-flex align-items-center gap-2 text-nowrap" :title="rowStatus(s).long">
                  <span class="rsm-dot rounded-circle" :class="dotClass(s)" aria-hidden="true"></span>
                  {{ rowStatus(s).label }}
                </span>
              </td>
              <td data-label="Players">
                <div class="rsm-num">{{ playersText(s) }}</div>
                <div class="progress rsm-players-bar" role="presentation">
                  <div class="progress-bar" :style="{ width: `${playersFill(s)}%` }"></div>
                </div>
              </td>
              <td data-label="FPS" class="text-end rsm-num">{{ fmtFps(s) }}</td>
              <td data-label="CPU" class="text-end rsm-num">{{ fmtCpu(s) }}</td>
              <td data-label="Memory" class="text-end rsm-num">{{ isRunning(s) ? fmtMem(s.mem_bytes) : '—' }}</td>
              <td data-label="Uptime" class="text-end rsm-num">{{ fmtUp(s) }}</td>
              <td
                data-label="Next restart"
                class="text-end rsm-num"
                :title="s.next_restart ? `${s.next_restart} (server time)` : ''"
              >{{ fmtNext(s) }}</td>
              <td class="rsm-cell-actions text-end text-nowrap">
                <template v-if="isRunning(s)">
                  <button
                    class="btn btn-sm btn-outline-secondary"
                    :disabled="!!pending[s.id]"
                    @click="action(s, 'stop')"
                  >Stop</button>
                  <button
                    class="btn btn-sm btn-outline-secondary ms-1"
                    :disabled="!!pending[s.id]"
                    @click="action(s, 'restart')"
                  >Restart</button>
                </template>
                <button
                  v-else
                  class="btn btn-sm btn-success"
                  :disabled="!s.server_files_ready || !!pending[s.id]"
                  :title="s.server_files_ready ? '' : `${branchLabel(s.branch)} server files are not downloaded yet`"
                  @click="action(s, 'start')"
                >Start</button>
                <button
                  class="btn btn-sm btn-outline-danger ms-1"
                  :aria-label="`Delete ${s.name}`"
                  @click="remove(s)"
                >Delete</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <p v-else-if="!error" class="text-secondary">Loading…</p>

    <!-- Restart all: every player on the host is disconnected, so confirm it -->
    <div v-if="restartAll.open" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">
              Restart {{ running.length }} running server{{ running.length === 1 ? '' : 's' }}?
            </h5>
            <button type="button" class="btn-close" @click="restartAll.open = false"></button>
          </div>
          <div class="modal-body">
            <p class="mb-2">{{ running.map((s) => s.name).join(', ') }}</p>
            <p class="small text-secondary mb-0">
              Everyone playing on them is disconnected, and each server takes a few minutes to
              load its mods and world again.
            </p>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" @click="restartAll.open = false">Cancel</button>
            <button class="btn btn-warning" :disabled="restartAll.busy" @click="confirmRestartAll">
              {{ restartAll.busy ? 'Restarting…' : 'Restart all' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Create modal -->
    <div v-if="showCreate" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">New server</h5>
            <button type="button" class="btn-close" @click="showCreate = false"></button>
          </div>
          <div class="modal-body">
            <div v-if="create.error" class="alert alert-danger py-2 small">{{ create.error }}</div>
            <div v-if="!hasTemplates" class="alert alert-info py-2 small">
              You need a <router-link :to="{ name: 'templates' }">template</router-link> first.
            </div>
            <template v-else>
              <div class="mb-3">
                <label class="form-label">Instance name</label>
                <input
                  v-model="create.name"
                  class="form-control"
                  placeholder="e.g. conflict-1"
                  autocomplete="off"
                  autocorrect="off"
                  spellcheck="false"
                />
              </div>
              <div class="mb-3">
                <label class="form-label">Template</label>
                <select v-model="create.template_id" class="form-select">
                  <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
                </select>
              </div>
              <div class="mb-3">
                <label class="form-label">Branch</label>
                <select v-model="create.branch" class="form-select">
                  <option value="stable">Stable (1874900)</option>
                  <option value="experimental">Experimental (1890870)</option>
                </select>
              </div>
              <div class="form-check mb-2">
                <input id="customPorts" v-model="create.customPorts" class="form-check-input" type="checkbox" />
                <label for="customPorts" class="form-check-label">
                  Set network ports manually
                  <small class="text-secondary d-block">Otherwise assigned automatically from the configured ranges</small>
                </label>
              </div>
              <div v-if="create.customPorts" class="row g-2">
                <div class="col-4">
                  <label class="form-label small">Game (UDP)</label>
                  <input v-model.number="create.game_port" type="number" class="form-control" placeholder="2001" />
                </div>
                <div class="col-4">
                  <label class="form-label small">A2S (UDP)</label>
                  <input v-model.number="create.a2s_port" type="number" class="form-control" placeholder="17777" />
                </div>
                <div class="col-4">
                  <label class="form-label small">RCON (UDP)</label>
                  <input v-model.number="create.rcon_port" type="number" class="form-control" placeholder="19999" />
                </div>
              </div>
            </template>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" @click="showCreate = false">Cancel</button>
            <button
              class="btn btn-primary"
              :disabled="!hasTemplates || create.busy || !create.name.trim()"
              @click="submitCreate"
            >
              {{ create.busy ? 'Creating…' : 'Create' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Delete modal: offer to also wipe the on-disk data, not just drop the
         container and orphan the folder (#131 follow-up). -->
    <div v-if="del.inst" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Delete "{{ del.inst.name }}"?</h5>
            <button type="button" class="btn-close" @click="del.inst = null"></button>
          </div>
          <div class="modal-body">
            <div v-if="del.error" class="alert alert-danger py-2 small">{{ del.error }}</div>

            <p class="mb-3">
              This instance's container is removed and it disappears from the list.
            </p>

            <div class="form-check mb-2">
              <input id="purgeData" v-model="del.purge" class="form-check-input" type="checkbox" />
              <label for="purgeData" class="form-check-label">
                Also delete all stored data from disk
                <small class="text-secondary d-block">
                  Baked mods, saved game, logs, configs — and this instance's saved game
                  backups. Otherwise they are left on the host and can no longer be
                  reached from the manager.
                </small>
              </label>
            </div>

            <!-- What is actually on disk, so the choice is informed (#79 data). -->
            <div v-if="del.data === null" class="small text-secondary">Checking disk usage…</div>
            <template v-else>
              <ul v-if="delItems.length" class="small mb-2">
                <li v-for="item in delItems" :key="item.target">
                  <span class="text-capitalize">{{ item.target }}</span>
                  — {{ fmtBytes(item.size_bytes) }} ({{ item.files }} file(s))
                </li>
              </ul>
              <p v-else class="small text-secondary mb-2">Nothing is stored on disk yet.</p>

              <div v-if="del.purge && delTotalBytes" class="alert alert-danger py-2 small mb-0">
                This permanently erases {{ fmtBytes(delTotalBytes) }} of data, including the
                saved game and every backup of it — the persistent world is gone for good
                and cannot be recovered. Download the backups you want to keep first.
              </div>
            </template>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" @click="del.inst = null">Cancel</button>
            <button class="btn btn-danger" :disabled="del.busy" @click="confirmDelete">
              {{ del.busy ? 'Deleting…' : (del.purge ? 'Delete instance & data' : 'Delete instance') }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rsm-dot {
  display: inline-block;
  width: 0.55rem;
  height: 0.55rem;
  flex: none;
}

/* Beside a line of text, sit on that line rather than the top of the box. */
.rsm-dot-text {
  margin-top: 0.5rem;
}

/* Four figures read as one strip: hairline dividers from the gap, not four cards. */
.rsm-totals {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  background: var(--bs-border-color);
  border: 1px solid var(--bs-border-color);
  border-radius: var(--bs-border-radius);
  overflow: hidden;
}

.rsm-total {
  background: var(--bs-body-bg);
  padding: 0.7rem 0.9rem 0.5rem;
}

.rsm-total-value {
  font-size: 1.45rem;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.rsm-total-unit {
  font-size: 0.85rem;
  font-weight: 400;
  color: var(--bs-secondary-color);
}

.rsm-num {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.rsm-players-bar {
  height: 0.25rem;
  width: 5.5rem;
  margin-top: 0.25rem;
}

.rsm-fleet thead th {
  font-weight: 500;
  color: var(--bs-secondary-color);
  white-space: nowrap;
}

.rsm-fleet tbody tr:last-child td {
  border-bottom: 0;
}

@media (max-width: 991.98px) {
  .rsm-totals {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

/* On a phone the table becomes one stacked block per server: name on top, the
   figures as labelled pairs, the buttons at the bottom. */
@media (max-width: 767.98px) {
  .rsm-fleet thead {
    display: none;
  }

  .rsm-fleet tbody tr {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.35rem 1rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--bs-border-color);
  }

  .rsm-fleet tbody tr:last-child {
    border-bottom: 0;
  }

  .rsm-fleet tbody td {
    display: block;
    padding: 0;
    border: 0;
    text-align: left !important;
  }

  .rsm-fleet td[data-label]::before {
    content: attr(data-label);
    display: block;
    font-size: 0.75rem;
    color: var(--bs-secondary-color);
  }

  .rsm-fleet .rsm-cell-name,
  .rsm-fleet .rsm-cell-actions {
    grid-column: 1 / -1;
  }

  .rsm-fleet .rsm-cell-actions {
    margin-top: 0.25rem;
  }
}
</style>
