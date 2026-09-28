<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import Sparkline from '../components/Sparkline.vue'
import { branchLabel } from '../overview'
import {
  guiUrl,
  hostLine,
  SEVERITY_DOT,
  serverRow,
  stackHealth,
  stackPorts,
  stackSummary,
  totalTiles,
} from './view'

// The Server Supervisor (#204, v0.67.0): every stack on this machine on one page,
// for whoever manages the machine. View only — there is not a single button here
// that changes anything; each team's controls stay in its own manager.

const overview = ref(null)
const signedIn = ref(null) // null until the first answer says which
const error = ref('')
const version = ref(null)
let poll = null

async function load() {
  try {
    overview.value = await api('/api/overview')
    signedIn.value = true
    error.value = ''
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      signedIn.value = false
      overview.value = null
    } else {
      error.value = e.message
    }
  }
}

// --- Sign in / out ---------------------------------------------------------------
const username = ref('')
const password = ref('')
const loginError = ref('')
const busy = ref(false)

async function signIn() {
  loginError.value = ''
  busy.value = true
  try {
    await api('/api/auth/login', {
      method: 'POST',
      body: { username: username.value, password: password.value },
    })
    password.value = ''
    await Promise.all([load(), loadVersion()])
  } catch (e) {
    loginError.value = e.message
  } finally {
    busy.value = false
  }
}

async function signOut() {
  await api('/api/auth/logout', { method: 'POST' })
  signedIn.value = false
  overview.value = null
}

async function loadVersion() {
  try {
    version.value = await api('/api/version')
  } catch {
    /* ignore */
  }
}

// --- The page ----------------------------------------------------------------------
const tiles = computed(() => totalTiles(overview.value))
const warnings = computed(() => overview.value?.warnings || [])
const stackList = computed(() => overview.value?.stacks || [])

onMounted(async () => {
  await Promise.all([load(), loadVersion()])
  poll = setInterval(() => {
    if (signedIn.value) load()
  }, 5000)
})
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <!-- Signing in -->
  <div
    v-if="signedIn === false"
    class="d-flex align-items-center justify-content-center"
    style="min-height: 100vh"
  >
    <div class="card shadow" style="width: 22rem">
      <div class="card-body p-4">
        <img src="/favicon.svg" width="64" height="64" alt="" class="d-block mx-auto mb-3" />
        <h1 class="h4 mb-1 text-center">Server Supervisor</h1>
        <p class="text-secondary text-center small mb-4">Every stack on this machine, read-only</p>
        <form @submit.prevent="signIn">
          <div class="mb-3">
            <label class="form-label" for="username">Username</label>
            <input id="username" v-model="username" class="form-control" autocomplete="username" required />
          </div>
          <div class="mb-3">
            <label class="form-label" for="password">Password</label>
            <input
              id="password"
              v-model="password"
              type="password"
              class="form-control"
              autocomplete="current-password"
              required
            />
          </div>
          <div v-if="loginError" class="alert alert-danger py-2 small">{{ loginError }}</div>
          <button class="btn btn-primary w-100" :disabled="busy">
            {{ busy ? 'Signing in…' : 'Sign in' }}
          </button>
        </form>
      </div>
    </div>
  </div>

  <div v-else-if="signedIn" class="container py-4">
    <!-- Header -->
    <header class="d-flex flex-wrap align-items-center gap-3 mb-3">
      <img src="/favicon.svg" width="40" height="40" alt="" />
      <div class="me-auto">
        <h1 class="h3 mb-0">Server Supervisor</h1>
        <div class="text-secondary small">
          {{ hostLine(overview?.host) || 'Every stack on this machine' }} · read-only
        </div>
      </div>
      <a
        v-if="version?.version"
        :href="version.repo_url"
        target="_blank"
        rel="noopener"
        class="small text-secondary text-decoration-none"
      >v{{ version.version }}</a>
      <button
        v-if="!version || version.auth_enabled"
        type="button"
        class="btn btn-sm btn-outline-secondary"
        @click="signOut"
      >Log out</button>
    </header>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>

    <template v-if="overview">
      <!-- Totals across every stack, each with the last hour -->
      <div class="rsm-tiles rsm-tiles-5 mb-3">
        <div v-for="t in tiles" :key="t.key" class="rsm-tile" :title="t.title">
          <div class="small text-secondary">{{ t.label }}</div>
          <div class="rsm-tile-value">
            {{ t.value }}<span class="rsm-tile-unit">{{ t.unit }}</span>
          </div>
          <Sparkline class="text-primary" :values="t.values" :label="`${t.label}, last hour`" />
        </div>
      </div>

      <!-- What needs whoever manages this machine -->
      <div v-if="warnings.length" class="card mb-3">
        <div class="card-header py-2 fw-semibold small">Needs attention · {{ warnings.length }}</div>
        <ul class="list-group list-group-flush">
          <li v-for="w in warnings" :key="w.id" class="list-group-item d-flex align-items-start gap-2 py-2">
            <span class="rsm-dot rsm-dot-text" :class="SEVERITY_DOT[w.severity]" aria-hidden="true"></span>
            <div>
              <div class="fw-semibold">{{ w.title }}</div>
              <div class="small text-secondary">{{ w.detail }}</div>
            </div>
          </li>
        </ul>
      </div>

      <div v-if="!stackList.length" class="card text-center py-5">
        <div class="card-body">
          <h2 class="h5">No stacks found</h2>
          <p class="text-secondary mb-0">
            No Reforger Server Manager runs on this machine yet, or only ones older than v0.65.0.
          </p>
        </div>
      </div>

      <!-- One card per stack -->
      <section v-for="st in stackList" :key="st.name" class="card mb-3">
        <div class="card-header d-flex flex-wrap align-items-center gap-2 py-2">
          <span class="rsm-dot" :class="stackHealth(st).dot" aria-hidden="true"></span>
          <h2 class="h6 mb-0 fw-semibold">{{ st.name }}</h2>
          <span class="small text-secondary me-auto">{{ stackHealth(st).text }}</span>
          <span
            v-for="p in stackPorts(st)"
            :key="p.label"
            class="badge border text-body-secondary fw-normal rsm-num"
            :title="`${p.label} ports (${p.proto})`"
          >{{ p.label }} {{ p.value }}</span>
          <a
            v-if="guiUrl(st)"
            :href="guiUrl(st)"
            target="_blank"
            rel="noopener"
            class="small ms-1"
            title="The team's own manager — reachable from here only if its GUI is not bound to 127.0.0.1"
          >Open GUI</a>
        </div>
        <div class="card-body py-2 small text-secondary border-bottom">
          {{ stackSummary(st) }}<template v-if="st.downloads"> · downloading server files</template>
        </div>
        <table v-if="st.servers.length" class="table rsm-fleet align-middle mb-0">
          <thead>
            <tr class="small">
              <th scope="col">Server</th>
              <th scope="col">Status</th>
              <th scope="col">Players</th>
              <th scope="col" class="text-end">FPS</th>
              <th scope="col" class="text-end">CPU</th>
              <th scope="col" class="text-end">Memory</th>
              <th scope="col" class="text-end">Uptime</th>
              <th scope="col" class="text-end">Game · A2S</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in st.servers" :key="s.container_id">
              <td class="rsm-cell-name">
                <span class="fw-semibold">{{ s.name }}</span>
                <div class="small text-secondary">
                  {{ branchLabel(s.branch) || '—' }}<template v-if="!s.named"> · named on its next start</template>
                </div>
              </td>
              <td data-label="Status">
                <span class="d-inline-flex align-items-center gap-2 text-nowrap" :title="serverRow(s).status.long">
                  <span class="rsm-dot" :class="serverRow(s).dot" aria-hidden="true"></span>
                  {{ serverRow(s).status.label }}
                </span>
              </td>
              <td data-label="Players">
                <div class="rsm-num">{{ serverRow(s).players }}</div>
                <div class="progress rsm-players-bar" role="presentation">
                  <div class="progress-bar" :style="{ width: `${serverRow(s).fill}%` }"></div>
                </div>
              </td>
              <td data-label="FPS" class="text-end rsm-num">{{ serverRow(s).fps }}</td>
              <td data-label="CPU" class="text-end rsm-num">{{ serverRow(s).cpu }}</td>
              <td data-label="Memory" class="text-end rsm-num">{{ serverRow(s).mem }}</td>
              <td data-label="Uptime" class="text-end rsm-num">{{ serverRow(s).uptime }}</td>
              <td data-label="Game · A2S" class="text-end rsm-num">{{ serverRow(s).ports }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <p class="small text-secondary">
        Read-only. Starting, stopping and changing servers is done in each team's own manager.
        Servers without a container (never started, or removed) are not shown.
      </p>
    </template>

    <p v-else-if="!error" class="text-secondary">Loading…</p>
  </div>
</template>

<style scoped>
.rsm-tiles-5 {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

@media (max-width: 991.98px) {
  .rsm-tiles-5 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
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

/* On a phone each server becomes a block: name on top, figures as labelled pairs. */
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

  .rsm-fleet .rsm-cell-name {
    grid-column: 1 / -1;
  }
}
</style>
