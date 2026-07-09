<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import Downloads from './Downloads.vue'

const router = useRouter()
const instances = ref([])
const summary = ref(null)
const templates = ref([])
const error = ref('')
const showCreate = ref(false)
const create = reactive({
  name: '', template_id: null, branch: 'stable',
  customPorts: false, game_port: null, a2s_port: null, rcon_port: null,
  busy: false, error: '',
})
let poll = null

const statusBadge = {
  running: 'text-bg-success',
  exited: 'text-bg-danger',
  created: 'text-bg-secondary',
  absent: 'text-bg-secondary',
  unknown: 'text-bg-warning',
}

async function load() {
  try {
    instances.value = await api('/api/instances')
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
  try {
    summary.value = await api('/api/instances/summary')
  } catch {
    /* keep last */
  }
}

const statusChip = {
  running: 'text-bg-success',
  exited: 'text-bg-danger',
  unknown: 'text-bg-warning',
}

async function openCreate() {
  create.name = ''
  create.branch = 'stable'
  create.error = ''
  try {
    templates.value = await api('/api/templates')
    create.template_id = templates.value[0]?.id ?? null
  } catch (e) {
    create.error = e.message
  }
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
    await api('/api/instances', { method: 'POST', body })
    showCreate.value = false
    await load()
  } catch (e) {
    create.error = e.message
  } finally {
    create.busy = false
  }
}

async function action(inst, verb) {
  try {
    await api(`/api/instances/${inst.id}/${verb}`, { method: 'POST' })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function remove(inst) {
  if (!confirm(`Delete instance "${inst.name}"? Its container is removed.`)) return
  try {
    await api(`/api/instances/${inst.id}`, { method: 'DELETE' })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

const hasTemplates = computed(() => templates.value.length > 0)

onMounted(async () => {
  await load()
  poll = setInterval(load, 5000)
})
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <div class="container">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">Server instances</h1>
      <button class="btn btn-primary" @click="openCreate">+ New instance</button>
    </div>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>

    <!-- Summary status bar (issue #12) -->
    <div v-if="summary && summary.total" class="card mb-3 bg-body-tertiary">
      <div class="card-body py-2">
        <div class="d-flex flex-wrap align-items-center gap-3">
          <div class="d-flex gap-3 me-2">
            <div><span class="fs-5 fw-semibold">{{ summary.running }}</span>
              <span class="text-secondary small">/ {{ summary.total }} running</span></div>
            <div><span class="fs-5 fw-semibold">{{ summary.players_total }}</span>
              <span class="text-secondary small">players online</span></div>
          </div>
          <div class="vr d-none d-md-block"></div>
          <div class="d-flex flex-wrap gap-2">
            <router-link
              v-for="s in summary.servers"
              :key="s.id"
              :to="{ name: 'instance-detail', params: { id: s.id } }"
              class="text-decoration-none d-inline-flex align-items-center gap-2 border rounded-pill ps-1 pe-2 py-1"
              :title="s.connect || 'PUBLIC_ADDRESS not set'"
            >
              <span class="badge rounded-pill" :class="statusChip[s.status] || 'text-bg-secondary'">
                {{ s.status === 'running' ? (s.players ?? '—') + ' 👤' : s.status }}
              </span>
              <span class="small text-body text-truncate" style="max-width: 16rem">{{ s.name }}</span>
            </router-link>
          </div>
        </div>
      </div>
    </div>

    <div v-if="!instances.length" class="card text-center text-secondary py-5">
      <div class="card-body">
        <p class="fs-1 mb-2">🖥️</p>
        <p class="mb-1">No server instances yet.</p>
        <p class="small mb-0">
          Create one from a <router-link to="/">template</router-link> to run an Arma
          Reforger server in its own container.
        </p>
      </div>
    </div>

    <div v-else class="row g-3">
      <div v-for="inst in instances" :key="inst.id" class="col-12 col-xl-6">
        <div class="card h-100">
          <div class="card-body">
            <div class="d-flex justify-content-between align-items-start mb-2">
              <div>
                <router-link
                  :to="{ name: 'instance-detail', params: { id: inst.id } }"
                  class="h5 mb-0 text-decoration-none"
                >{{ inst.name }}</router-link>
                <div class="small text-secondary">
                  {{ inst.template_name || '—' }}
                  <span class="badge ms-1" :class="inst.branch === 'stable' ? 'text-bg-success' : 'text-bg-warning'">
                    {{ inst.branch }}
                  </span>
                </div>
              </div>
              <span class="badge" :class="statusBadge[inst.status] || 'text-bg-secondary'">
                {{ inst.status }}
              </span>
            </div>

            <div class="small text-secondary mb-3">
              game :{{ inst.game_port }} · A2S :{{ inst.a2s_port }} · RCON :{{ inst.rcon_port }}
            </div>

            <div v-if="!inst.server_files_ready" class="alert alert-warning py-1 px-2 small mb-2">
              {{ inst.branch }} server files not downloaded —
              <a href="#server-files">get them below</a> before starting.
            </div>

            <div class="d-flex gap-2 flex-wrap">
              <button
                v-if="inst.status !== 'running'"
                class="btn btn-sm btn-success"
                :disabled="!inst.server_files_ready"
                @click="action(inst, 'start')"
              >Start</button>
              <button v-else class="btn btn-sm btn-outline-secondary" @click="action(inst, 'stop')">
                Stop
              </button>
              <button class="btn btn-sm btn-outline-primary" @click="action(inst, 'restart')">
                Restart
              </button>
              <router-link
                class="btn btn-sm btn-outline-info"
                :to="{ name: 'instance-detail', params: { id: inst.id } }"
              >Logs</router-link>
              <button class="btn btn-sm btn-outline-danger ms-auto" @click="remove(inst)">
                Delete
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Create modal -->
    <div v-if="showCreate" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">New instance</h5>
            <button type="button" class="btn-close" @click="showCreate = false"></button>
          </div>
          <div class="modal-body">
            <div v-if="create.error" class="alert alert-danger py-2 small">{{ create.error }}</div>
            <div v-if="!hasTemplates" class="alert alert-info py-2 small">
              You need a <router-link to="/">template</router-link> first.
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
  </div>

  <!-- Server files (formerly the Downloads tab) live at the bottom here now -->
  <Downloads id="server-files" class="border-top pt-4 mt-4" />
</template>
