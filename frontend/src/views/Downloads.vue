<script setup>
import { onMounted, onUnmounted, reactive, nextTick } from 'vue'
import { api } from '../api'

const state = reactive({ branches: [], docker: true, error: '', steamcmd_image: '', server_image: '' })
const logs = reactive({})
const updateInfo = reactive({})
const checking = reactive({})
const sockets = {}
const logPanes = {}

async function checkUpdate(branch) {
  checking[branch] = true
  try {
    updateInfo[branch] = await api(`/api/serverfiles/${branch}/check-update`)
  } catch (e) {
    state.error = e.message
  } finally {
    checking[branch] = false
  }
}

async function removeFiles(branch) {
  if (!confirm(`Delete all ${branch} server files? You'll need to download them again before running a ${branch} server.`)) return
  try {
    await api(`/api/serverfiles/${branch}`, { method: 'DELETE' })
    delete updateInfo[branch]
    await refresh()
  } catch (e) {
    state.error = e.message
  }
}

const badge = { stable: 'text-bg-success', experimental: 'text-bg-warning' }

function fmtBytes(n) {
  if (!n) return ''
  return (n / 1e9).toFixed(2) + ' GB'
}

function fmtDate(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : ''
}

function branchState(name) {
  return state.branches.find((b) => b.branch === name)
}

async function refresh() {
  try {
    const data = await api('/api/serverfiles')
    state.docker = data.docker
    state.branches = data.branches
    state.steamcmd_image = data.steamcmd_image
    state.server_image = data.server_image
    state.error = ''
    for (const b of data.branches) {
      if (b.job?.status === 'running') connect(b.branch)
    }
  } catch (e) {
    state.error = e.message
  }
}

function scrollLog(branch) {
  nextTick(() => {
    const el = logPanes[branch]
    if (el) el.scrollTop = el.scrollHeight
  })
}

function connect(branch) {
  if (sockets[branch]) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/serverfiles/${branch}/ws`)
  sockets[branch] = ws
  ws.onmessage = (msg) => {
    const ev = JSON.parse(msg.data)
    const b = branchState(branch)
    if (!b) return
    if (ev.type === 'snapshot') {
      Object.assign(b, ev.state)
      logs[branch] = ev.log || []
      scrollLog(branch)
    } else if (ev.type === 'log') {
      const lines = (logs[branch] ||= [])
      lines.push(ev.line)
      if (lines.length > 500) lines.shift()
      scrollLog(branch)
    } else if (ev.type === 'progress') {
      if (b.job) Object.assign(b.job, ev)
    } else if (ev.type === 'status') {
      b.job = ev.job
      if (ev.installed) b.installed = ev.installed
    }
  }
  ws.onclose = () => {
    delete sockets[branch]
    refresh()
  }
}

async function startDownload(branch) {
  const b = branchState(branch)
  try {
    b.job = await api(`/api/serverfiles/${branch}/download`, { method: 'POST' })
    logs[branch] = []
    connect(branch)
  } catch (e) {
    state.error = e.message
  }
}

onMounted(refresh)
onUnmounted(() => {
  for (const ws of Object.values(sockets)) ws.close()
})
</script>

<template>
  <div class="container">
    <h1 class="h3 mb-3">Server files</h1>

    <div v-if="!state.docker" class="alert alert-danger">
      The Docker daemon is not reachable from the manager — check that
      <code>/var/run/docker.sock</code> is mounted. Downloads are disabled.
    </div>
    <div v-if="state.error" class="alert alert-warning py-2">{{ state.error }}</div>

    <div class="row g-3">
      <div v-for="b in state.branches" :key="b.branch" class="col-12 col-lg-6">
        <div class="card h-100">
          <div class="card-body">
            <div class="d-flex justify-content-between align-items-center mb-2">
              <h2 class="h5 mb-0">{{ b.label }}</h2>
              <span class="badge" :class="badge[b.branch]">app {{ b.app_id }}</span>
            </div>

            <p v-if="b.installed" class="text-secondary small mb-2">
              Installed — build {{ b.installed.build_id }}
              <template v-if="b.installed.size_bytes"> · {{ fmtBytes(b.installed.size_bytes) }}</template>
              <template v-if="b.installed.last_updated"> · updated {{ fmtDate(b.installed.last_updated) }}</template>
            </p>
            <p v-else class="text-secondary small mb-2">Not installed.</p>

            <div v-if="b.job && b.job.status === 'running'" class="mb-2">
              <div class="d-flex justify-content-between small mb-1">
                <span class="text-capitalize">{{ b.job.phase }}</span>
                <span>
                  {{ b.job.percent.toFixed(1) }}%
                  <template v-if="b.job.bytes_total">
                    ({{ fmtBytes(b.job.bytes_done) }} / {{ fmtBytes(b.job.bytes_total) }})
                  </template>
                </span>
              </div>
              <div class="progress" style="height: 0.75rem">
                <div
                  class="progress-bar progress-bar-striped progress-bar-animated"
                  :style="{ width: b.job.percent + '%' }"
                ></div>
              </div>
            </div>

            <div v-if="b.job && b.job.status === 'error'" class="alert alert-danger py-2 small mb-2">
              {{ b.job.error }}
            </div>
            <div v-if="b.job && b.job.status === 'success'" class="alert alert-success py-2 small mb-2">
              Download completed.
            </div>

            <div v-if="updateInfo[b.branch]" class="small mb-2">
              <span class="text-secondary">Latest Steam build:</span>
              {{ updateInfo[b.branch].latest_build || 'unknown' }}
              <span
                v-if="updateInfo[b.branch].update_available"
                class="badge text-bg-warning ms-1"
              >Update available</span>
              <span
                v-else-if="updateInfo[b.branch].installed_build"
                class="badge text-bg-success ms-1"
              >Up to date</span>
            </div>

            <div class="d-flex gap-2">
              <button
                class="btn btn-primary"
                :disabled="!state.docker || (b.job && b.job.status === 'running')"
                @click="startDownload(b.branch)"
              >
                <span
                  v-if="b.job && b.job.status === 'running'"
                  class="spinner-border spinner-border-sm me-1"
                ></span>
                {{ b.installed ? 'Update server files' : 'Download server files' }}
              </button>
              <button
                class="btn btn-outline-secondary"
                :disabled="!state.docker || checking[b.branch]"
                @click="checkUpdate(b.branch)"
              >
                {{ checking[b.branch] ? 'Checking…' : 'Check for updates' }}
              </button>
              <button
                v-if="b.installed"
                class="btn btn-outline-danger ms-auto"
                :disabled="!state.docker || (b.job && b.job.status === 'running')"
                @click="removeFiles(b.branch)"
              >
                Remove
              </button>
            </div>

            <pre
              v-if="logs[b.branch]?.length"
              :ref="(el) => (logPanes[b.branch] = el)"
              class="mt-3 p-2 bg-black text-light rounded small mb-0"
              style="max-height: 16rem; overflow-y: auto; white-space: pre-wrap"
            >{{ logs[b.branch].join('\n') }}</pre>
          </div>
        </div>
      </div>
    </div>

    <p class="text-secondary small mt-3 mb-0">
      SteamCMD image: <code>{{ state.steamcmd_image }}</code> ·
      Server image: <code>{{ state.server_image }}</code>
    </p>
  </div>
</template>
