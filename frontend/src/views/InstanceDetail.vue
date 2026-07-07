<script setup>
import { onMounted, onUnmounted, ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const props = defineProps({ id: { type: [String, Number], required: true } })
const router = useRouter()

const inst = ref(null)
const error = ref('')
const logLines = ref([])
const follow = ref(true)
const logPane = ref(null)
let ws = null
let poll = null

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
  connectLogs()
  poll = setInterval(loadInstance, 5000)
})
onUnmounted(() => {
  if (ws) ws.close()
  clearInterval(poll)
})
</script>

<template>
  <div class="container">
    <router-link to="/" class="btn btn-sm btn-outline-secondary mb-3">← Instances</router-link>

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
          <button v-if="inst.status !== 'running'" class="btn btn-success" @click="action('start')">Start</button>
          <button v-else class="btn btn-outline-secondary" @click="action('stop')">Stop</button>
          <button class="btn btn-outline-primary" @click="action('restart')">Restart</button>
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
                <div class="fw-semibold small">Auto-restart on crash</div>
                <div class="text-secondary small">Restarts if the container exits</div>
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
    </div>
  </div>
</template>
