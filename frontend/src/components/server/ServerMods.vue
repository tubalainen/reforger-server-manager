<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api'
import { formatBytes } from '../../format'
import ModIdLink from '../ModIdLink.vue'
import { modStateLabel, modsSummary } from '../../modVersions'

// The template's mods on this server: the version on disk against the Workshop's
// newest release, and the two ways to move them (#246). Every ordinary start
// keeps the versions on disk; the daily restart may update them (Automation, above),
// Force update does it now, and Redownload and bake starts from scratch.
const props = defineProps({
  inst: { type: Object, required: true },
  players: { type: Number, default: null },
  // starting | online | … from the live stats: the mods are on disk once online
  serverState: { type: String, default: '' },
})
const emit = defineEmits(['update'])

const running = computed(() => props.inst.status === 'running')
const info = ref(null)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const busy = ref('')
const confirming = ref('') // '' | 'update' | 'redownload'
const checkedAt = ref('')

async function load() {
  loading.value = true
  try {
    info.value = await api(`/api/instances/${props.inst.id}/mods`)
    checkedAt.value = new Date().toLocaleTimeString()
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
onMounted(load)
defineExpose({ load })
// What is on disk changes when the server starts, stops or finishes downloading.
watch(() => `${props.inst.status}|${props.serverState}`, (now, before) => {
  if (before !== undefined) load()
})

const summary = computed(() => (info.value ? modsSummary(info.value) : ''))
const playersLine = computed(() => {
  if (!running.value) return ''
  if (props.players == null) return 'Anyone on the server is disconnected.'
  if (props.players === 0) return 'Nobody is on the server right now.'
  return `${props.players} player${props.players === 1 ? '' : 's'} online will be disconnected.`
})

async function run(kind) {
  busy.value = kind
  confirming.value = ''
  error.value = ''
  notice.value = ''
  try {
    const path = kind === 'update' ? 'update' : 'redownload'
    const res = await api(`/api/instances/${props.inst.id}/mods/${path}`, { method: 'POST' })
    notice.value =
      kind === 'update'
        ? 'Started with the newest mod versions. The server downloads what changed before it comes online.'
        : `Deleted ${res.removed_mods} mod folder(s) (${formatBytes(res.size_bytes)}). ` +
          'The server is downloading and baking them again — expect a long start.'
    emit('update', res)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}

const unlocked = computed(() => (info.value?.mods || []).filter((m) => !m.locked).length)

const STATE_CLASS = {
  behind: 'text-bg-warning',
  current: 'text-bg-success',
  locked: 'text-bg-secondary',
  missing: 'text-bg-info',
  unreadable: 'text-bg-danger',
  unknown: 'text-bg-light border',
}
</script>

<template>
  <div class="card">
    <div class="card-header d-flex justify-content-between align-items-center py-2">
      <span class="fw-semibold small">
        Mods
        <span v-if="info?.updates_available" class="badge text-bg-warning ms-1">
          {{ info.updates_available }} update{{ info.updates_available === 1 ? '' : 's' }} available
        </span>
      </span>
      <button class="btn btn-sm btn-outline-secondary" :disabled="loading" @click="load">
        {{ loading ? 'Checking…' : 'Check again' }}
      </button>
    </div>
    <div class="card-body">
      <p class="text-secondary small mb-2">
        Every start keeps the mod versions already downloaded.
        <template v-if="info?.auto_update && info?.scheduled">
          The daily restart updates them to the newest Workshop release.
        </template>
        <template v-else>
          They change only with Force update<template v-if="info && !info.scheduled">, or a daily
          restart with mod updates on</template>.
        </template>
        A version locked in the template never changes.
      </p>

      <div v-if="info?.all_locked" class="alert alert-secondary py-2 small">
        Every mod in this server's template is locked to a version, so neither the daily
        restart nor Force update changes them. To follow new Workshop releases, unlock the
        mods in the template (the Mods step's <em>Unlock all</em>).
      </div>
      <div v-if="error" class="alert alert-warning py-2 small">{{ error }}</div>
      <div v-if="notice" class="alert alert-success py-2 small">{{ notice }}</div>

      <template v-if="info">
        <p v-if="!info.mods.length" class="text-secondary small">This server's template has no mods.</p>
        <div v-else class="table-responsive mb-2">
          <table class="table table-sm align-middle small mb-0">
            <thead>
              <tr>
                <th scope="col">Mod</th>
                <th scope="col">On this server</th>
                <th scope="col">Workshop</th>
                <th scope="col"><span class="visually-hidden">State</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in info.mods" :key="m.modId">
                <td class="text-break">
                  <span class="d-block">{{ m.name || m.modId }}</span>
                  <small class="text-secondary"><ModIdLink :mod-id="m.modId" :name="m.name || ''" /></small>
                </td>
                <td class="rsm-num">{{ m.installed || '—' }}</td>
                <td class="rsm-num">{{ m.latest || '—' }}</td>
                <td class="text-nowrap">
                  <span class="badge" :class="STATE_CLASS[m.state]">{{ modStateLabel(m.state, m.locked) }}</span>
                  <span v-if="m.locked && m.state !== 'locked'" class="badge text-bg-secondary ms-1">
                    Locked at {{ m.locked }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="text-secondary small">
          {{ summary }}
          <template v-if="checkedAt"> Checked {{ checkedAt }}.</template>
        </p>
      </template>
      <p v-else-if="loading" class="text-secondary small">Asking the Workshop for the newest versions…</p>

      <div class="d-flex flex-wrap gap-2">
        <button
          class="btn btn-outline-primary"
          :disabled="!!busy || !inst.server_files_ready"
          @click="confirming = 'update'"
        >
          {{ busy === 'update' ? 'Updating…' : 'Force update' }}
        </button>
        <button
          class="btn btn-outline-danger"
          :disabled="!!busy || !inst.server_files_ready"
          @click="confirming = 'redownload'"
        >
          {{ busy === 'redownload' ? 'Redownloading…' : 'Redownload and bake' }}
        </button>
      </div>
      <small class="d-block text-secondary mt-2">
        <strong>Force update</strong> restarts the server with the newest version of each mod
        and downloads only what changed. <strong>Redownload and bake</strong> deletes this
        template's mods and downloads them all again: use it when a download is broken.
      </small>
    </div>

    <div v-if="confirming" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-body">
            <template v-if="confirming === 'update'">
              <h2 class="h6">Update the mods of "{{ inst.name }}" now?</h2>
              <p v-if="running" class="small mb-2">
                The server restarts with the newest Workshop version of each of its template's
                mods. {{ playersLine }}
              </p>
              <p v-else class="small mb-2">
                The server is stopped. Force update <strong>starts it</strong>, since only a
                running server downloads mods, and it stays running afterwards.
              </p>
              <p v-if="info && !unlocked" class="small text-warning mb-0">
                Every mod is locked in the template, so this changes no versions; it only
                restarts the server.
              </p>
              <p v-else-if="info && !info.updates_available" class="small text-secondary mb-0">
                The last check found no newer versions, so this may change nothing.
              </p>
            </template>
            <template v-else>
              <h2 class="h6">Redownload and bake the mods of "{{ inst.name }}"?</h2>
              <p class="small mb-2">
                <template v-if="running">The server stops. </template>Its template's mods and what
                they depend on are deleted<template v-if="info?.redownload">
                  ({{ info.redownload.folders }} folder(s), {{ formatBytes(info.redownload.size_bytes) }})</template>,
                then it starts and downloads them all again. The first start takes a long time.
              </p>
              <p v-if="running" class="small mb-2">{{ playersLine }}</p>
              <p class="small text-secondary mb-0">
                Mods from templates this server used before are left alone; clear those under
                Maintenance. Saved games are not touched.
              </p>
            </template>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" @click="confirming = ''">Cancel</button>
            <button
              v-if="confirming === 'update'"
              class="btn btn-primary"
              @click="run('update')"
            >
              {{ running ? 'Restart and update' : 'Start and update' }}
            </button>
            <button v-else class="btn btn-danger" @click="run('redownload')">
              Delete and redownload
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
