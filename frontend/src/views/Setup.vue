<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useBranchDownload, useImagePull } from '../jobs'
import { setSetupPending } from '../router'
import JobProgress from '../components/JobProgress.vue'

// The first-run setup screen (#241). A brand-new install has nothing a server
// needs, so the GUI opens here once and fetches it: the SteamCMD image, the game's
// server files (which SteamCMD downloads), and the runtime image every server runs
// from. The backend decides "brand new" once and remembers it; finishing or
// skipping this page is what ends it.

const router = useRouter()
const page = reactive({ loaded: false, docker: true, error: '', busy: false })

const steamcmd = useImagePull('/api/serverfiles/steamcmd-image', { onClose: () => refresh() })
const runtime = useImagePull('/api/serverfiles/image', { onClose: () => refresh() })
const stable = useBranchDownload('stable', { onClose: () => refresh() })
const experimental = useBranchDownload('experimental', { onClose: () => refresh() })
// Experimental is opt-in: most servers run Stable, and it is another full download.
const withExperimental = ref(false)

const BRANCHES = [
  { key: 'stable', label: 'Stable', dl: stable },
  { key: 'experimental', label: 'Experimental', dl: experimental },
]

const running = (job) => job?.status === 'running'
const branches = computed(() => (withExperimental.value ? BRANCHES : BRANCHES.slice(0, 1)))

const steps = computed(() => [
  {
    key: 'steamcmd',
    title: 'SteamCMD',
    text: "Valve's command-line Steam client, as a Docker image. The manager runs it to download the game's server files from Steam.",
    done: steamcmd.state.present,
    jobs: [steamcmd.state.job],
  },
  {
    key: 'files',
    title: 'Arma Reforger server files',
    text: 'The dedicated server itself, downloaded from Steam by SteamCMD. The biggest of the three; it can take a while.',
    done: branches.value.every((b) => b.dl.state.installed),
    jobs: branches.value.map((b) => b.dl.state.job),
  },
  {
    key: 'runtime',
    title: 'Server runtime image',
    text: 'The Docker image every server runs in. It runs the server files above.',
    done: runtime.state.present,
    jobs: [runtime.state.job],
  },
])
const allDone = computed(() => steps.value.every((s) => s.done))
const anyRunning = computed(() => steps.value.some((s) => s.jobs.some(running)))

async function refresh() {
  try {
    const data = await api('/api/serverfiles')
    page.docker = data.docker
    steamcmd.state.present = data.steamcmd_image_present
    steamcmd.state.job = data.steamcmd_image_job
    runtime.state.present = data.server_image_present
    runtime.state.job = data.server_image_job
    for (const b of data.branches) {
      const dl = BRANCHES.find((x) => x.key === b.branch)?.dl
      if (!dl) continue
      dl.state.installed = b.installed
      dl.state.job = b.job
      if (running(b.job)) dl.connect()
    }
    // Downloaded before (or still going) — show it, rather than hiding it again.
    if (experimental.state.installed || running(experimental.state.job)) withExperimental.value = true
    for (const img of [steamcmd, runtime]) {
      if (running(img.state.job)) img.connect()
    }
  } catch (e) {
    page.error = e.message
  } finally {
    page.loaded = true
  }
}

async function run(start) {
  page.error = ''
  try {
    await start()
  } catch (e) {
    page.error = e.message
  }
}

function pullSteamcmd() {
  return run(steamcmd.pull)
}
function pullRuntime() {
  return run(runtime.pull)
}
async function downloadFiles() {
  for (const b of branches.value) {
    if (!b.dl.state.installed && !running(b.dl.state.job)) await run(b.dl.download)
  }
}

// "Download everything": both images at once, then the server files as soon as
// SteamCMD is there to fetch them.
const queued = ref(false)
async function downloadAll() {
  if (!steamcmd.state.present && !running(steamcmd.state.job)) await pullSteamcmd()
  if (!runtime.state.present && !running(runtime.state.job)) await pullRuntime()
  if (steamcmd.state.present) await downloadFiles()
  else queued.value = true
}
watch(
  () => steamcmd.state.present,
  (present) => {
    if (present && queued.value) {
      queued.value = false
      downloadFiles()
    }
  },
)
// A failed SteamCMD pull cancels the queued download; its error is on screen.
watch(
  () => steamcmd.state.job?.status,
  (status) => {
    if (status === 'error') queued.value = false
  },
)

// Finishing and skipping both end the setup screen for good.
async function leave(to) {
  page.busy = true
  try {
    await api('/api/system/setup/done', { method: 'POST' })
    setSetupPending(false)
    router.replace(to)
  } catch (e) {
    page.error = e.message
    page.busy = false
  }
}

onMounted(refresh)
onUnmounted(() => {
  for (const x of [steamcmd, runtime, stable, experimental]) x.close()
})
</script>

<template>
  <div class="container py-4" style="max-width: 46rem">
    <div class="d-flex align-items-center gap-3 mb-3">
      <img src="/favicon.svg" width="48" height="48" alt="" />
      <div>
        <h1 class="h3 mb-0">Welcome to Reforger Server Manager</h1>
        <div class="text-secondary">Let's get this machine ready to run Arma Reforger servers.</div>
      </div>
    </div>

    <p v-if="page.loaded && allDone" class="mb-3">
      All set. Next, build a server template: the scenario, mods and settings a server runs.
    </p>
    <p v-else class="mb-3">
      This is a new install, so the three things every server needs aren't on this machine
      yet. Download them now; they keep downloading if you leave this page.
    </p>

    <div v-if="!page.docker" class="alert alert-danger">
      The Docker daemon is not reachable from the manager — check that
      <code>/var/run/docker.sock</code> is mounted. Downloads are disabled.
    </div>
    <div v-if="page.error" class="alert alert-warning py-2 d-flex align-items-start gap-2">
      <span class="flex-grow-1">{{ page.error }}</span>
      <button type="button" class="btn-close" aria-label="Dismiss" @click="page.error = ''"></button>
    </div>

    <div v-if="page.loaded" class="d-flex flex-wrap align-items-center gap-2 mb-3">
      <button
        class="btn btn-primary"
        :disabled="!page.docker || allDone || anyRunning || queued"
        @click="downloadAll"
      >
        <span v-if="anyRunning || queued" class="spinner-border spinner-border-sm me-1"></span>
        {{ allDone ? 'Everything is downloaded' : anyRunning || queued ? 'Downloading…' : 'Download everything' }}
      </button>
      <span v-if="queued" class="small text-secondary">
        The server files start as soon as SteamCMD is pulled.
      </span>
    </div>

    <ol v-if="page.loaded" class="list-unstyled mb-4">
      <li v-for="(s, i) in steps" :key="s.key" class="card mb-2">
        <div class="card-body">
          <div class="d-flex align-items-start gap-3">
            <span
              class="rsm-step-num"
              :class="s.done ? 'text-bg-success' : 'text-bg-secondary'"
              aria-hidden="true"
            >{{ s.done ? '✓' : i + 1 }}</span>
            <div class="flex-grow-1 min-w-0">
              <div class="d-flex flex-wrap align-items-center gap-2">
                <h2 class="h6 mb-0 me-auto">{{ s.title }}</h2>
                <span v-if="s.done" class="badge text-bg-success">Downloaded</span>

                <button
                  v-else-if="s.key === 'steamcmd'"
                  class="btn btn-sm btn-outline-primary"
                  :disabled="!page.docker || running(steamcmd.state.job)"
                  @click="pullSteamcmd"
                >{{ running(steamcmd.state.job) ? 'Pulling…' : 'Pull image' }}</button>

                <button
                  v-else-if="s.key === 'files'"
                  class="btn btn-sm btn-outline-primary"
                  :disabled="!page.docker || !steamcmd.state.present || s.jobs.some(running)"
                  :title="steamcmd.state.present ? '' : 'Pull SteamCMD first'"
                  @click="downloadFiles"
                >{{ s.jobs.some(running) ? 'Downloading…' : 'Download' }}</button>

                <button
                  v-else
                  class="btn btn-sm btn-outline-primary"
                  :disabled="!page.docker || running(runtime.state.job)"
                  @click="pullRuntime"
                >{{ running(runtime.state.job) ? 'Pulling…' : 'Pull image' }}</button>
              </div>
              <p class="small text-secondary mb-2 mt-1">{{ s.text }}</p>

              <template v-if="s.key === 'files'">
                <div v-for="b in branches" :key="b.key" class="mb-1">
                  <div class="small">
                    {{ b.label }}:
                    <span v-if="b.dl.state.installed" class="text-success">
                      build {{ b.dl.state.installed.build_id }}
                    </span>
                    <span v-else-if="!running(b.dl.state.job)" class="text-secondary">not downloaded</span>
                  </div>
                  <JobProgress :job="b.dl.state.job" />
                </div>
                <div class="form-check mt-2">
                  <input
                    id="withExperimental"
                    v-model="withExperimental"
                    class="form-check-input"
                    type="checkbox"
                    :disabled="!!experimental.state.installed || running(experimental.state.job)"
                  />
                  <label for="withExperimental" class="form-check-label small">
                    Also download the Experimental branch
                    <span class="text-secondary d-block">
                      Only for servers that run the game's public test build. You can add it
                      later under System › Server files.
                    </span>
                  </label>
                </div>
              </template>
              <JobProgress v-else :job="s.jobs[0]" />
            </div>
          </div>
        </div>
      </li>
    </ol>
    <p v-else class="text-secondary">Loading…</p>

    <div class="d-flex flex-wrap align-items-center gap-2">
      <button
        class="btn btn-success"
        :disabled="!allDone || page.busy"
        @click="leave({ name: 'template-new' })"
      >Next: build a server template</button>
      <button
        class="btn btn-link text-secondary ms-auto"
        :disabled="page.busy"
        @click="leave({ name: 'instances' })"
      >{{ allDone ? 'Go to Servers' : 'Skip for now' }}</button>
    </div>
    <p v-if="!allDone" class="small text-secondary mt-2 mb-0">
      Skipping is fine: everything here is also under System › Server files, and this
      page won't show again.
    </p>
  </div>
</template>

<style scoped>
.rsm-step-num {
  flex: none;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.85rem;
  font-weight: 600;
}

.min-w-0 {
  min-width: 0;
}
</style>
