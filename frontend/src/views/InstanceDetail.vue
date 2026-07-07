<script setup>
import { onMounted, onUnmounted, ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const props = defineProps({ id: { type: [String, Number], required: true } })
const router = useRouter()

const inst = ref(null)
const stats = ref(null)
const error = ref('')
const logLines = ref([])
const follow = ref(true)
const logPane = ref(null)
let ws = null
let poll = null

function fmtMem(bytes) {
  if (!bytes) return '—'
  const mb = bytes / 1048576
  return mb >= 1024 ? (mb / 1024).toFixed(2) + ' GB' : mb.toFixed(0) + ' MB'
}

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
function fmtBytes(n) {
  if (!n) return '0 B'
  return n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : (n / 1024).toFixed(0) + ' KB'
}
function fmtTime(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : ''
}
function downloadLog(path) {
  window.location.href = `/api/instances/${props.id}/logfiles/download?path=${encodeURIComponent(path)}`
}

const statusBadge = {
  running: 'text-bg-success',
  exited: 'text-bg-danger',
  created: 'text-bg-secondary',
  absent: 'text-bg-secondary',
  unknown: 'text-bg-warning',
}

async function loadInstance() {
  try {
    inst.value = await api(`/api/instances/${props.id}`)
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
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
  try {
    inst.value = await api(`/api/instances/${props.id}/${verb}`, { method: 'POST' })
    // reconnect logs after start/restart (a new container may exist)
    if (verb !== 'stop') setTimeout(connectLogs, 800)
  } catch (e) {
    error.value = e.message
  }
}

async function toggleAutoRestart() {
  try {
    inst.value = await api(`/api/instances/${props.id}/auto-restart`, {
      method: 'PUT',
      body: { auto_restart: !inst.value.auto_restart },
    })
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  await loadInstance()
  await loadStats()
  await loadLogFiles()
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
      <div class="d-flex justify-content-between align-items-center mb-2">
        <h1 class="h3 mb-0">
          {{ inst.name }}
          <span class="badge align-middle" :class="statusBadge[inst.status] || 'text-bg-secondary'">
            {{ inst.status }}
          </span>
        </h1>
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
        <router-link to="/downloads">download them on the Downloads tab</router-link> before starting.
      </div>

      <!-- Live status strip -->
      <div v-if="stats" class="card mb-3">
        <div class="card-body py-2">
          <div class="row text-center g-2 small">
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
            </div>
            <div class="col-12 col-md-4">
              <div class="text-secondary">Connect</div>
              <div class="fw-semibold text-truncate">
                {{ stats.connect || (stats.public_address ? '' : 'set PUBLIC_ADDRESS') || '—' }}
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
              </div>
            </div>
          </div>
        </div>
        <div class="col-md-4">
          <div class="card h-100">
            <div class="card-body d-flex align-items-center justify-content-between">
              <div>
                <div class="fw-semibold small">Keep running</div>
                <div class="text-secondary small">Auto-restart on crash and on Docker/host restart</div>
              </div>
              <div class="form-check form-switch">
                <input
                  class="form-check-input"
                  type="checkbox"
                  role="switch"
                  :checked="inst.auto_restart"
                  @change="toggleAutoRestart"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center py-2">
          <span class="fw-semibold small">Server log</span>
          <div class="form-check form-switch mb-0">
            <input id="follow" v-model="follow" class="form-check-input" type="checkbox" role="switch" />
            <label for="follow" class="form-check-label small">Follow</label>
          </div>
        </div>
        <pre
          ref="logPane"
          class="card-body bg-black text-light small mb-0 rounded-bottom"
          style="height: 55vh; overflow-y: auto; white-space: pre-wrap"
        >{{ logLines.join('\n') || '// waiting for log output…' }}</pre>
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
  </div>
</template>
