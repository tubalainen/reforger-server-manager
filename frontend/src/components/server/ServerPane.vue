<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api'
import { isErrorLine } from '../../log'
import { attentionItems } from '../../overview'
import { SERVER_TABS, tabParams } from '../../server'
import { serverStatus } from '../../status'
import DeleteServerDialog from '../DeleteServerDialog.vue'
import ServerConsole from './ServerConsole.vue'
import ServerOverview from './ServerOverview.vue'
import ServerPlayers from './ServerPlayers.vue'
import ServerSaves from './ServerSaves.vue'
import ServerSettings from './ServerSettings.vue'

// One server's page (#189): a header with its state and the buttons that change it,
// the one thing it needs from you, and five tabs. The page mounts afresh for each
// server (it is keyed by id), so nothing from one server leaks into the next.
const props = defineProps({
  id: { type: [String, Number], required: true },
  tab: { type: String, required: true },
  summary: { type: Object, default: null },
  updates: { type: Array, default: () => [] },
})
const emit = defineEmits(['changed'])
const router = useRouter()

const inst = ref(null)
const stats = ref(null)
const error = ref('')
let poll = null

async function loadInstance() {
  try {
    inst.value = await api(`/api/instances/${props.id}`)
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}
async function loadStats() {
  try {
    stats.value = await api(`/api/instances/${props.id}/stats`)
  } catch {
    /* transient; keep last */
  }
}

// "running" means the container is up; the server inside it may still be loading
// mods and the world, and cannot be joined until it says it is online (#76).
// `pending` covers the stop/start/restart the user just asked for, which the old
// server can spend tens of seconds ignoring.
const pending = ref('')
const view = computed(() => serverStatus(inst.value?.status, stats.value?.server_state, pending.value))
const running = computed(() => inst.value?.status === 'running')
const dotClass = computed(() => view.value.cls.replace('text-bg-', 'bg-'))

// This server's row in the summary: its player limit, scenario and history live there.
const row = computed(() => (props.summary?.servers || []).find((s) => String(s.id) === String(props.id)))
const maxPlayers = computed(() => row.value?.max_players ?? null)
const history = computed(() => props.summary?.history || [])

async function action(verb) {
  pending.value = verb
  try {
    inst.value = await api(`/api/instances/${props.id}/${verb}`, { method: 'POST' })
    // Refresh the live stats at once: otherwise the status keeps showing the old
    // server's "online" for up to a poll interval after a restart (#76).
    await loadStats()
    emit('changed')
    // reconnect logs after start/restart (a new container may exist)
    if (verb !== 'stop') setTimeout(connectLogs, 800)
  } catch (e) {
    error.value = e.message
  } finally {
    pending.value = ''
  }
}

// --- The log stream ------------------------------------------------------------
// Held here rather than in the Console tab, so what has arrived survives switching
// tabs, and the Overview can show the last few lines of the same stream.
const logLines = ref([])
let ws = null
function connectLogs() {
  if (ws) ws.close()
  logLines.value = []
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/instances/${props.id}/logs`)
  ws.onmessage = (msg) => {
    const ev = JSON.parse(msg.data)
    logLines.value.push(ev.line)
    if (logLines.value.length > 2000) logLines.value.shift()
  }
}
const errorCount = computed(() => logLines.value.filter(isErrorLine).length)

// --- Header bits -----------------------------------------------------------------
const connect = computed(() => stats.value?.connect || '')
const copied = ref(false)
async function copyAddress() {
  try {
    await navigator.clipboard.writeText(connect.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    /* clipboard blocked (no https / no permission) — the address is selectable anyway */
  }
}

// The one thing this server needs, if anything, with its button (same rules as the
// Servers overview's list).
const attention = computed(() => {
  if (!inst.value) return []
  const mine = props.updates.filter((u) => u.branch === inst.value.branch)
  return attentionItems([inst.value], mine, false)
})
async function runAttention(item) {
  const a = item.action
  if (a.kind === 'restart') return action('restart')
  if (a.kind === 'update') {
    // Start the download, then watch it where downloads are shown (#177).
    try {
      await api(`/api/serverfiles/${a.branch}/download`, { method: 'POST' })
    } catch (e) {
      error.value = e.message
      return
    }
  }
  router.push({ name: 'server-files' })
}
const ATTENTION_BUTTON = { restart: 'Restart now', update: 'Update server files', 'server-files': 'Server files' }

// The ⋯ menu. Bootstrap's dropdown needs its JavaScript, which the app does not ship
// (#175), so Vue opens and closes it.
const menuOpen = ref(false)
const menuEl = ref(null)
function closeMenuOnOutsideClick(e) {
  if (menuOpen.value && menuEl.value && !menuEl.value.contains(e.target)) menuOpen.value = false
}

const deleting = ref(false)
function onDeleted() {
  deleting.value = false
  emit('changed')
  router.push({ name: 'instances' })
}

// On a phone the tab row scrolls sideways; keep the open tab in view.
const tabsEl = ref(null)
function revealActiveTab() {
  nextTick(() => {
    tabsEl.value?.querySelector('.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  })
}
watch(() => props.tab, revealActiveTab)
// The counts on Console and Players arrive after the first paint and widen the row.
watch(() => [errorCount.value > 0, stats.value?.players != null], revealActiveTab)

function tabBadge(key) {
  if (key === 'console' && errorCount.value) return { text: errorCount.value, cls: 'text-bg-danger' }
  if (key === 'players' && running.value && stats.value?.players != null) {
    return { text: stats.value.players, cls: 'text-bg-secondary' }
  }
  return null
}

onMounted(async () => {
  document.addEventListener('click', closeMenuOnOutsideClick)
  await Promise.all([loadInstance(), loadStats()])
  revealActiveTab()
  connectLogs()
  poll = setInterval(() => {
    loadInstance()
    loadStats()
  }, 5000)
})
onUnmounted(() => {
  document.removeEventListener('click', closeMenuOnOutsideClick)
  if (ws) ws.close()
  clearInterval(poll)
})
</script>

<template>
  <div>
    <div v-if="error && !inst" class="alert alert-warning">
      {{ error }}
      <div><router-link :to="{ name: 'instances' }">Back to all servers</router-link></div>
    </div>

    <template v-if="inst">
      <!-- Header: who, what state, how to reach it, what you can do -->
      <header class="d-flex flex-wrap align-items-start gap-3 mb-3">
        <div class="me-auto" style="min-width: 0">
          <div class="d-flex flex-wrap align-items-center gap-2">
            <h1 class="h3 mb-0 text-break">{{ inst.name }}</h1>
            <span class="d-inline-flex align-items-center gap-2 small fw-semibold" :title="view.note">
              <span class="rsm-dot" :class="dotClass" aria-hidden="true"></span>
              <span
                v-if="view.starting"
                class="spinner-border spinner-border-sm"
                role="status"
                aria-hidden="true"
                style="width: .7em; height: .7em; border-width: .15em"
              ></span>
              {{ view.long }}
            </span>
          </div>
          <div class="d-flex flex-wrap align-items-center gap-2 small text-secondary mt-1">
            <span>{{ row?.scenario_name || inst.template_name || '—' }}</span>
            <span aria-hidden="true">·</span>
            <span class="text-capitalize">{{ inst.branch }}</span>
            <template v-if="connect">
              <span aria-hidden="true">·</span>
              <code class="text-body">{{ connect }}</code>
              <button class="btn btn-link btn-sm p-0 align-baseline" @click="copyAddress">
                {{ copied ? 'Copied' : 'Copy' }}
              </button>
            </template>
          </div>
        </div>

        <div class="d-flex gap-2">
          <button
            v-if="!running"
            class="btn btn-success"
            :disabled="!inst.server_files_ready || !!pending"
            @click="action('start')"
          >Start</button>
          <template v-else>
            <button class="btn btn-outline-secondary" :disabled="!!pending" @click="action('stop')">Stop</button>
            <button class="btn btn-outline-primary" :disabled="!!pending" @click="action('restart')">Restart</button>
          </template>
          <div ref="menuEl" class="dropdown">
            <button
              class="btn btn-outline-secondary"
              aria-label="More actions"
              :aria-expanded="menuOpen"
              @click="menuOpen = !menuOpen"
            >⋯</button>
            <ul class="dropdown-menu dropdown-menu-end" :class="{ show: menuOpen }">
              <li>
                <router-link
                  class="dropdown-item"
                  :to="{ name: 'instance-detail', params: tabParams(inst.id, 'settings') }"
                  @click="menuOpen = false"
                >Rename or change settings</router-link>
              </li>
              <li><hr class="dropdown-divider" /></li>
              <li>
                <button class="dropdown-item text-danger" @click="menuOpen = false; deleting = true">
                  Delete server…
                </button>
              </li>
            </ul>
          </div>
        </div>
      </header>

      <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>

      <!-- The one thing it needs, with its button -->
      <div
        v-for="item in attention"
        :key="item.key"
        class="alert alert-warning d-flex flex-wrap align-items-center gap-2 py-2"
      >
        <span class="me-auto">
          <!-- The server's own name is the page title already; other subjects are not. -->
          <strong v-if="item.action.kind !== 'restart'">{{ item.subject }} · </strong>{{ item.text }}
        </span>
        <button
          class="btn btn-sm btn-warning"
          :disabled="item.action.kind === 'restart' && !!pending"
          @click="runAttention(item)"
        >{{ ATTENTION_BUTTON[item.action.kind] }}</button>
      </div>

      <nav ref="tabsEl" class="nav nav-underline rsm-tabs mb-3" aria-label="Server sections">
        <router-link
          v-for="t in SERVER_TABS"
          :key="t.key"
          :to="{ name: 'instance-detail', params: tabParams(inst.id, t.key) }"
          class="nav-link d-inline-flex align-items-center gap-2"
          :class="{ active: tab === t.key }"
          :aria-current="tab === t.key ? 'page' : undefined"
        >
          {{ t.label }}
          <span v-if="tabBadge(t.key)" class="badge rounded-pill" :class="tabBadge(t.key).cls">
            {{ tabBadge(t.key).text }}
          </span>
        </router-link>
      </nav>

      <ServerOverview
        v-if="tab === 'overview'"
        :inst="inst"
        :stats="stats"
        :view="view"
        :log-lines="logLines"
        :history="history"
        :max-players="maxPlayers"
        @action="action"
      />
      <ServerConsole
        v-else-if="tab === 'console'"
        :id="inst.id"
        :log-lines="logLines"
        @clear="logLines = []"
      />
      <ServerPlayers
        v-else-if="tab === 'players'"
        :inst="inst"
        :stats="stats"
        :max-players="maxPlayers"
      />
      <ServerSaves
        v-else-if="tab === 'saves'"
        :inst="inst"
        @changed="loadInstance(); emit('changed')"
      />
      <ServerSettings
        v-else-if="tab === 'settings'"
        :inst="inst"
        :connect="connect"
        :address-detected="!!stats?.public_address_detected"
        @update="(v) => { inst = v; emit('changed') }"
        @changed="loadInstance(); emit('changed')"
        @delete="deleting = true"
      />

      <DeleteServerDialog
        v-if="deleting"
        :server="inst"
        @close="deleting = false"
        @deleted="onDeleted"
      />
    </template>

    <p v-else-if="!error" class="text-secondary">Loading…</p>
  </div>
</template>
