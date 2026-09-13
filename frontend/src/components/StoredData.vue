<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { formatBytes } from '../format'

// What a server has written to disk, and clearing it (#79). The saved game lives on
// the Saves tab and the mods and logs under Settings › Maintenance (#189), so each
// place shows only its own `targets`. Always rendered: a failed load shows an error
// rather than hiding the whole feature (#85).
const props = defineProps({
  id: { type: [String, Number], required: true },
  running: { type: Boolean, default: false },
  targets: { type: Array, required: true }, // any of: mods | saves | logs
  title: { type: String, default: 'Stored data' },
  serverName: { type: String, default: '' },
})
const emit = defineEmits(['changed'])

// Editing a template's mods and just restarting can leave the server running the
// old content: it reuses the addons it already downloaded and baked. Wiping that
// makes the next start rebuild it.
const DATA_KINDS = {
  mods: {
    label: 'Downloaded & baked mods',
    hint: 'The addons this server downloaded for its template. Clear this when you changed the template\'s mods but the server still runs the old ones — the next start downloads and bakes them again.',
    danger: false,
  },
  saves: {
    label: 'Saved game data',
    hint: 'The world your players built: the save points, and the databases the scenario and its mods keep beside them. Clearing it starts from scratch — there is no undo, so take a backup first.',
    danger: true,
  },
  logs: {
    label: 'Logs & crash reports',
    hint: 'Past sessions\' console logs and crash dumps. Safe to clear; only history is lost.',
    danger: false,
  },
}

const fmtBytes = formatBytes
const dataInfo = ref(null)
const dataPicked = ref([])
const dataBusy = ref(false)
const dataNotice = ref('')
const dataError = ref('')
const confirmClear = ref(false)

// Toggled explicitly rather than with v-model on the array: v-model reads the
// array it was last patched with, so two boxes ticked before a re-render both
// build on the same stale copy and only the last one survives.
function pickData(target, on) {
  const next = new Set(dataPicked.value)
  if (on) next.add(target)
  else next.delete(target)
  dataPicked.value = [...next]
}

async function loadData() {
  try {
    dataInfo.value = await api(`/api/instances/${props.id}/data`)
    dataError.value = ''
  } catch (e) {
    dataError.value = e.message
  }
}
defineExpose({ load: loadData })

const dataItems = computed(() =>
  (dataInfo.value?.items || [])
    .filter((i) => props.targets.includes(i.target))
    .map((i) => ({ ...i, ...DATA_KINDS[i.target] })),
)
const pickedItems = computed(() => dataItems.value.filter((i) => dataPicked.value.includes(i.target)))
const clearingSaves = computed(() => dataPicked.value.includes('saves'))

// The save row is the output of the template's persistence settings, so say what
// those settings are — and, when there is no save, why there might not be (#160).
const persistenceNote = computed(() => {
  const p = dataInfo.value?.persistence
  if (!p) return ''
  const saves = (dataInfo.value?.items || []).find((i) => i.target === 'saves')
  const empty = !saves?.files
  const where = p.template_name ? `template "${p.template_name}"` : 'this template'
  if (p.persistence) {
    const hive = `${where} configures persistence (hive id ${p.hive_id}).`
    return empty
      ? `${hive} Nothing has been saved yet — a save point appears once the scenario writes one.`
      : hive
  }
  const engine = `${where} does not configure persistence, so the server uses the engine's own defaults — which still save.`
  return empty
    ? `${engine} Nothing has been saved yet: not every scenario writes a save (Game Master, for one, does not).`
    : engine
})

async function clearData() {
  dataBusy.value = true
  try {
    const res = await api(`/api/instances/${props.id}/data/clear`, {
      method: 'POST',
      body: { targets: dataPicked.value },
    })
    const freed = res.removed.reduce((n, r) => n + r.size_bytes, 0)
    dataNotice.value =
      `Cleared ${res.removed.map((r) => DATA_KINDS[r.target].label.toLowerCase()).join(', ')}` +
      ` (${fmtBytes(freed)} freed).` +
      (dataPicked.value.includes('mods')
        ? ' The next start will download and bake the mods again — expect it to take a while.'
        : '')
    dataPicked.value = []
    confirmClear.value = false
    await loadData()
    emit('changed')
  } catch (e) {
    dataError.value = e.message
    confirmClear.value = false
  } finally {
    dataBusy.value = false
  }
}

onMounted(loadData)
</script>

<template>
  <div class="card">
    <div class="card-header d-flex justify-content-between align-items-center py-2">
      <span class="fw-semibold small">{{ title }}</span>
      <button class="btn btn-sm btn-outline-secondary" @click="loadData">Refresh</button>
    </div>
    <div class="card-body">
      <p class="text-secondary small mb-2">
        Clearing something makes the server rebuild it from the template the next time it
        starts.
      </p>

      <div v-if="dataError" class="alert alert-warning py-2 small mb-0">
        Could not read what this server has stored: {{ dataError }}
        <div class="text-secondary">Use Refresh to try again.</div>
      </div>

      <template v-else-if="dataInfo">
        <p v-if="dataInfo.host_path" class="text-secondary small">
          None of it lives inside the container image — it is all kept on the host at
          <code class="text-break">{{ dataInfo.host_path }}</code> and mounted in, so it
          survives container rebuilds and manager updates.
          <template v-if="targets.includes('saves')">
            To keep a copy of the world, use the backups above rather than copying files by hand.
          </template>
        </p>

        <div v-if="dataNotice" class="alert alert-success py-2 small">{{ dataNotice }}</div>

        <div v-if="running" class="alert alert-secondary py-2 small mb-3">
          Stop the server to clear its data — pulling the addons or the save out from
          under a running server would corrupt both.
        </div>

        <div class="list-group list-group-flush mb-3">
          <!-- input + sibling label[for], not a label wrapping the input: the
               wrapping form makes a click on the box activate the label too. -->
          <div
            v-for="item in dataItems"
            :key="item.target"
            class="list-group-item d-flex gap-3 align-items-start px-0"
            :class="{ 'opacity-50': running }"
          >
            <input
              :id="`clear-${item.target}`"
              class="form-check-input mt-1 flex-shrink-0"
              type="checkbox"
              :checked="dataPicked.includes(item.target)"
              :disabled="running || !item.files"
              @change="pickData(item.target, $event.target.checked)"
            />
            <label :for="`clear-${item.target}`" class="flex-grow-1">
              <span class="fw-semibold">{{ item.label }}</span>
              <span class="badge text-bg-secondary ms-2">
                {{ item.files ? fmtBytes(item.size_bytes) : 'empty' }}
              </span>
              <span v-if="item.danger && item.files" class="badge text-bg-danger ms-1">
                destructive
              </span>
              <small class="d-block text-secondary">{{ item.hint }}</small>
              <small v-if="item.paths.length" class="d-block text-secondary text-break">
                <code>{{ item.paths.slice(0, 6).join(', ') }}</code>
                <span v-if="item.paths.length > 6">+{{ item.paths.length - 6 }} more</span>
                · {{ item.files }} file(s)
              </small>
              <small
                v-if="item.target === 'saves' && persistenceNote"
                class="d-block text-secondary fst-italic"
              >{{ persistenceNote }}</small>
            </label>
          </div>
        </div>

        <button
          class="btn btn-outline-danger"
          :disabled="!dataPicked.length || running || dataBusy"
          @click="confirmClear = true"
        >
          {{ dataBusy ? 'Clearing…' : 'Clear selected data' }}
        </button>
      </template>

      <p v-else class="text-secondary small mb-0">Loading…</p>
    </div>

    <!-- Spell out exactly what is about to be deleted (issue #79) -->
    <div v-if="confirmClear" class="modal d-block" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-body">
            <h2 class="h6">Clear stored data{{ serverName ? ` for "${serverName}"` : '' }}?</h2>
            <p class="small text-secondary mb-2">This deletes, permanently:</p>
            <ul class="small">
              <li v-for="item in pickedItems" :key="item.target">
                <strong>{{ item.label }}</strong> — {{ fmtBytes(item.size_bytes) }}
                ({{ item.files }} file(s))
                <small v-if="item.paths.length" class="d-block text-secondary text-break">
                  <code>{{ item.paths.slice(0, 8).join(', ') }}</code>
                  <span v-if="item.paths.length > 8"> +{{ item.paths.length - 8 }} more</span>
                </small>
              </li>
            </ul>
            <div v-if="clearingSaves" class="alert alert-danger py-2 small mb-2">
              Every save point goes with it: this server's persistent world is gone for
              good, and the scenario starts over from scratch. Nothing here can bring it back.
            </div>
            <p class="small text-secondary mb-0">
              The server rebuilds what it needs on the next start — re-downloading and
              re-baking mods can take several minutes.
            </p>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" @click="confirmClear = false">Cancel</button>
            <button class="btn btn-danger" :disabled="dataBusy" @click="clearData">
              {{ dataBusy ? 'Clearing…' : 'Clear it' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
