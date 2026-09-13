<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { api } from '../../api'
import { formatBytes, formatTimestamp } from '../../format'
import { isErrorLine } from '../../log'

// The live server log, full height, with an errors-only filter; the log and crash
// files on disk sit underneath (#189). The lines themselves are streamed by the
// server page, so switching tabs does not drop what has arrived.
const props = defineProps({
  id: { type: [String, Number], required: true },
  logLines: { type: Array, required: true },
})
const emit = defineEmits(['clear'])

const follow = ref(true)
const errorsOnly = ref(false)
const logPane = ref(null)

// Keep each line's position so the error filter can hide lines without re-keying.
const lines = computed(() => props.logLines.map((text, i) => ({ i, text, error: isErrorLine(text) })))
const errorCount = computed(() => lines.value.filter((l) => l.error).length)
const shown = computed(() => (errorsOnly.value ? lines.value.filter((l) => l.error) : lines.value))

function toBottom() {
  nextTick(() => {
    if (logPane.value) logPane.value.scrollTop = logPane.value.scrollHeight
  })
}
watch(() => props.logLines.length, () => { if (follow.value) toBottom() })
watch(follow, (on) => { if (on) toBottom() })

const logFiles = ref([])
async function loadLogFiles() {
  try {
    logFiles.value = await api(`/api/instances/${props.id}/logfiles`)
  } catch {
    /* ignore */
  }
}
function downloadLog(path) {
  window.location.href = `/api/instances/${props.id}/logfiles/download?path=${encodeURIComponent(path)}`
}

onMounted(() => {
  toBottom()
  loadLogFiles()
})
</script>

<template>
  <div class="d-grid gap-3">
    <div class="card">
      <div class="card-header d-flex flex-wrap justify-content-between align-items-center gap-2 py-2">
        <div class="btn-group btn-group-sm" role="group" aria-label="Which lines">
          <button
            class="btn"
            :class="!errorsOnly ? 'btn-secondary' : 'btn-outline-secondary'"
            :aria-pressed="!errorsOnly"
            @click="errorsOnly = false"
          >All</button>
          <button
            class="btn"
            :class="errorsOnly ? 'btn-secondary' : 'btn-outline-secondary'"
            :aria-pressed="errorsOnly"
            @click="errorsOnly = true"
          >Errors · {{ errorCount }}</button>
        </div>
        <div class="d-flex align-items-center gap-3">
          <div class="form-check form-switch mb-0">
            <input id="follow" v-model="follow" class="form-check-input" type="checkbox" role="switch" />
            <label for="follow" class="form-check-label small">Follow</label>
          </div>
          <button
            class="btn btn-sm btn-outline-secondary py-0"
            :disabled="!logLines.length"
            title="Clear the log window (new output keeps streaming)"
            @click="emit('clear')"
          >Clear</button>
        </div>
      </div>
      <!-- Line by line so errors can be painted red (#108). -->
      <div ref="logPane" class="rsm-log rsm-console rounded-bottom p-2">
        <template v-if="shown.length">
          <div v-for="l in shown" :key="l.i" :class="{ 'log-error': l.error }">{{ l.text || ' ' }}</div>
        </template>
        <div v-else-if="errorsOnly && logLines.length" class="log-muted">// no errors in this window</div>
        <div v-else class="log-muted">// waiting for log output…</div>
      </div>
    </div>

    <!-- Log & crash files -->
    <div class="card">
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
                <td class="rsm-num">{{ formatBytes(f.size) }}</td>
                <td class="rsm-num">{{ formatTimestamp(f.modified) }}</td>
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
</template>

<style scoped>
.rsm-console {
  height: 62vh;
  min-height: 18rem;
}
</style>
