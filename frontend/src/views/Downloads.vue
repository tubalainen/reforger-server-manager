<script setup>
import { onMounted, onUnmounted, reactive, nextTick } from 'vue'
import { api } from '../api'
import { formatBytes, formatTimestamp } from '../format'
import { useBranchDownload, useImagePull } from '../jobs'
import HelpTip from '../components/HelpTip.vue'
import JobProgress from '../components/JobProgress.vue'

const state = reactive({ branches: [], docker: true, error: '', steamcmd_image: '', server_image: '' })
const updateInfo = reactive({})
const checking = reactive({})
const logPanes = {}
// The daily update check and its two toggles (#177).
const autoUpdate = reactive({
  auto_download: false, auto_restart: false,
  last_check_at: null, next_check_at: null, checking: false,
  branches: [], busy: false,
})
let autoPoll = null

function scrollPane(key) {
  nextTick(() => {
    const el = logPanes[key]
    if (el) el.scrollTop = el.scrollHeight
  })
}

// The SteamCMD image the server-file downloads run in (#241), and the reforger
// runtime image each server's container is created from (issue #50).
const steamcmd = useImagePull('/api/serverfiles/steamcmd-image', {
  onUpdate: () => scrollPane('steamcmd'),
  onClose: () => refresh(),
})
const runtime = useImagePull('/api/serverfiles/image', {
  onUpdate: () => scrollPane('runtime'),
  onClose: () => refresh(),
})

const images = [
  {
    key: 'steamcmd', pull: steamcmd, title: 'SteamCMD image',
    setting: 'steamcmd_image', env: 'STEAMCMD_IMAGE',
    blurb: 'Downloads the game server files from Steam, pulled once.',
    about: 'The Docker image each server-file download runs in:',
    done: 'Image pulled — you can download the server files now.',
  },
  {
    key: 'runtime', pull: runtime, title: 'Server runtime image',
    setting: 'server_image', env: 'REFORGER_SERVER_IMAGE',
    blurb: 'Every server runs from this image, pulled once.',
    about: "The Docker image each server's container is created from:",
    done: 'Image pulled — you can start your servers now.',
  },
]

// One download per branch, made the first time the branch is listed.
const files = {}
function filesFor(branch) {
  return (files[branch] ||= useBranchDownload(branch, {
    onUpdate: () => scrollPane(branch),
    onClose: () => refresh(),
  }))
}

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

function applyAutoUpdate(data) {
  Object.assign(autoUpdate, data)
  // The daily check already knows each branch's latest Steam build, so show it
  // in the branch cards instead of making the user press "Check for updates".
  for (const b of autoUpdate.branches) {
    if (b.latest_build) updateInfo[b.branch] = b
  }
}

async function loadAutoUpdate() {
  try {
    applyAutoUpdate(await api('/api/serverfiles/auto-update'))
  } catch (e) {
    state.error = e.message
  }
}

async function saveAutoUpdate(patch) {
  autoUpdate.busy = true
  try {
    applyAutoUpdate(await api('/api/serverfiles/auto-update', { method: 'PUT', body: patch }))
  } catch (e) {
    state.error = e.message
  } finally {
    autoUpdate.busy = false
  }
}

async function checkNow() {
  autoUpdate.busy = true
  try {
    applyAutoUpdate(await api('/api/serverfiles/auto-update/check', { method: 'POST' }))
    // The check runs in the background (a steamcmd container per branch), so
    // poll until it reports itself finished.
    if (!autoPoll) autoPoll = setInterval(pollCheck, 3000)
  } catch (e) {
    state.error = e.message
  } finally {
    autoUpdate.busy = false
  }
}

async function pollCheck() {
  await loadAutoUpdate()
  if (!autoUpdate.checking) {
    clearInterval(autoPoll)
    autoPoll = null
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

const fmtBytes = (n) => formatBytes(n, { base: 1000, empty: '' })
const fmtDate = formatTimestamp

async function refresh() {
  try {
    const data = await api('/api/serverfiles')
    state.docker = data.docker
    // Each branch card reads its download's live state.
    state.branches = data.branches.map((b) => ({ ...b, files: filesFor(b.branch) }))
    state.steamcmd_image = data.steamcmd_image
    state.server_image = data.server_image
    steamcmd.state.present = data.steamcmd_image_present
    steamcmd.state.job = data.steamcmd_image_job
    runtime.state.present = data.server_image_present
    runtime.state.job = data.server_image_job
    state.error = ''
    await loadAutoUpdate()
    for (const b of data.branches) {
      const f = filesFor(b.branch)
      f.state.job = b.job
      f.state.installed = b.installed
      if (b.job?.status === 'running') f.connect()
    }
    for (const img of images) {
      if (img.pull.state.job?.status === 'running') img.pull.connect()
    }
  } catch (e) {
    state.error = e.message
  }
}

// Start a pull or a download; what it ran into goes in the page's alert.
async function run(start) {
  try {
    await start()
  } catch (e) {
    state.error = e.message
  }
}

onMounted(refresh)
onUnmounted(() => {
  for (const f of Object.values(files)) f.close()
  steamcmd.close()
  runtime.close()
  if (autoPoll) clearInterval(autoPoll)
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

    <!-- The two Docker images: SteamCMD, which the server-file downloads run in
         (#241), and the runtime image every server is created from (#50).
         Both are separate from the game's server files below. -->
    <div v-for="img in images" :key="img.key" class="card mb-3">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-start mb-2 flex-wrap gap-2">
          <div>
            <h2 class="h5 mb-1">{{ img.title }}</h2>
            <p class="text-secondary small mb-0">
              {{ img.blurb }}
              <HelpTip :label="`About the ${img.title}`">
                <p>{{ img.about }} <code>{{ state[img.setting] }}</code>.</p>
                <p>
                  It is separate from the game's server files below. Set
                  <code>{{ img.env }}</code> in <code>.env</code> to use another one.
                </p>
              </HelpTip>
            </p>
          </div>
          <span
            class="badge align-self-center"
            :class="img.pull.state.present ? 'text-bg-success' : 'text-bg-secondary'"
          >{{ img.pull.state.present ? 'Installed' : 'Not installed' }}</span>
        </div>

        <JobProgress :job="img.pull.state.job" />
        <div
          v-if="img.pull.state.job && img.pull.state.job.status === 'success'"
          class="alert alert-success py-2 small mb-2"
        >{{ img.done }}</div>

        <button
          class="btn btn-primary"
          :disabled="!state.docker || img.pull.state.job?.status === 'running'"
          @click="run(img.pull.pull)"
        >
          <span
            v-if="img.pull.state.job?.status === 'running'"
            class="spinner-border spinner-border-sm me-1"
          ></span>
          {{ img.pull.state.present ? 'Re-pull image' : 'Pull image' }}
        </button>

        <pre
          v-if="img.pull.state.log.length"
          :ref="(el) => (logPanes[img.key] = el)"
          class="mt-3 p-2 bg-black text-light rounded small mb-0"
          style="max-height: 16rem; overflow-y: auto; white-space: pre-wrap"
        >{{ img.pull.state.log.join('\n') }}</pre>
      </div>
    </div>

    <!-- Automatic updates: the daily check, and how far it is allowed to go
         on its own (#177). -->
    <div class="card mb-3">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-start mb-2 flex-wrap gap-2">
          <div>
            <h2 class="h5 mb-1">Automatic updates</h2>
            <p class="text-secondary small mb-0">
              Once a day the manager asks Steam whether a newer server release exists for
              the branches you have installed.
            </p>
          </div>
          <button
            class="btn btn-outline-secondary btn-sm"
            :disabled="!state.docker || autoUpdate.checking || autoUpdate.busy"
            @click="checkNow"
          >
            <span v-if="autoUpdate.checking" class="spinner-border spinner-border-sm me-1"></span>
            {{ autoUpdate.checking ? 'Checking…' : 'Check now' }}
          </button>
        </div>

        <div class="form-check">
          <input
            id="autoDownload"
            class="form-check-input"
            type="checkbox"
            :checked="autoUpdate.auto_download"
            :disabled="autoUpdate.busy"
            @change="saveAutoUpdate({ auto_download: $event.target.checked })"
          />
          <label for="autoDownload" class="form-check-label">
            Download new server files automatically
            <small class="text-secondary d-block">
              Off: nothing is downloaded on its own — you get a prompt on the Servers page
              when an update is waiting.
            </small>
          </label>
        </div>
        <div class="form-check mt-2">
          <input
            id="autoRestartOnUpdate"
            class="form-check-input"
            type="checkbox"
            :checked="autoUpdate.auto_restart"
            :disabled="autoUpdate.busy || !autoUpdate.auto_download"
            @change="saveAutoUpdate({ auto_restart: $event.target.checked })"
          />
          <label for="autoRestartOnUpdate" class="form-check-label">
            Restart my servers onto the new build once it is downloaded
            <small class="text-secondary d-block">
              Every server on that branch that should be running is restarted, which
              disconnects the players on it. Without this the files are updated but each
              server keeps running the build it started with.
            </small>
          </label>
        </div>

        <p class="text-secondary small mb-0 mt-3">
          <template v-if="autoUpdate.last_check_at">
            Last checked {{ fmtDate(autoUpdate.last_check_at) }}<template v-if="autoUpdate.next_check_at">
              · next {{ fmtDate(autoUpdate.next_check_at) }}</template>.
          </template>
          <template v-else>Not checked yet — the first check runs shortly after the manager starts.</template>
        </p>
      </div>
    </div>

    <div class="row g-3">
      <div v-for="b in state.branches" :key="b.branch" class="col-12 col-lg-6">
        <div class="card h-100">
          <div class="card-body">
            <div class="d-flex justify-content-between align-items-center mb-2">
              <h2 class="h5 mb-0">{{ b.label }}</h2>
              <span class="badge" :class="badge[b.branch]">app {{ b.app_id }}</span>
            </div>

            <p v-if="b.files.state.installed" class="text-secondary small mb-2">
              Installed — build {{ b.files.state.installed.build_id }}
              <template v-if="b.files.state.installed.size_bytes">
                · {{ fmtBytes(b.files.state.installed.size_bytes) }}</template>
              <template v-if="b.files.state.installed.last_updated">
                · updated {{ fmtDate(b.files.state.installed.last_updated) }}</template>
            </p>
            <p v-else class="text-secondary small mb-2">Not installed.</p>

            <JobProgress :job="b.files.state.job" />
            <div
              v-if="b.files.state.job?.status === 'success'"
              class="alert alert-success py-2 small mb-2"
            >
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
                :disabled="!state.docker || b.files.state.job?.status === 'running'"
                @click="run(b.files.download)"
              >
                <span
                  v-if="b.files.state.job?.status === 'running'"
                  class="spinner-border spinner-border-sm me-1"
                ></span>
                {{ b.files.state.installed ? 'Update server files' : 'Download server files' }}
              </button>
              <button
                class="btn btn-outline-secondary"
                :disabled="!state.docker || checking[b.branch]"
                @click="checkUpdate(b.branch)"
              >
                {{ checking[b.branch] ? 'Checking…' : 'Check for updates' }}
              </button>
              <button
                v-if="b.files.state.installed"
                class="btn btn-outline-danger ms-auto"
                :disabled="!state.docker || b.files.state.job?.status === 'running'"
                @click="removeFiles(b.branch)"
              >
                Remove
              </button>
            </div>

            <pre
              v-if="b.files.state.log.length"
              :ref="(el) => (logPanes[b.branch] = el)"
              class="mt-3 p-2 bg-black text-light rounded small mb-0"
              style="max-height: 16rem; overflow-y: auto; white-space: pre-wrap"
            >{{ b.files.state.log.join('\n') }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
