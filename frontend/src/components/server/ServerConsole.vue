<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { api } from '../../api'
import { formatBytes, formatTimestamp } from '../../format'
import { isErrorLine } from '../../log'
import AskAiDialog from './AskAiDialog.vue'
import ModDownloadProgress from './ModDownloadProgress.vue'

// The live server log, full height, with an errors-only filter; the log and crash
// files on disk sit underneath (#189). The lines themselves are streamed by the
// server page, so switching tabs does not drop what has arrived.
const props = defineProps({
  id: { type: [String, Number], required: true },
  logLines: { type: Array, required: true },
  download: { type: Object, default: null }, // stats.mod_download while starting (#235)
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

// "Ask AI" on error lines, only when the AI service in .env is set up and answering
// (#254). The manager checks that and remembers the answer for a few minutes.
const ai = ref(null)
const asking = ref('')
// ...and on the log files, text ones only: a crash dump is binary.
const askingFile = ref('')
const isTextLog = (path) => /\.(log|rpt|txt)$/i.test(path)
async function loadAi() {
  try {
    ai.value = await api('/api/system/ai')
  } catch {
    ai.value = null
  }
}

onMounted(() => {
  toBottom()
  loadLogFiles()
  loadAi()
})
</script>

<template>
  <div class="d-grid gap-3">
    <!-- The server logs nothing while it downloads its mods; show that it is (#235). -->
    <div v-if="download" class="card">
      <div class="card-body py-2">
        <ModDownloadProgress :download="download" />
      </div>
    </div>

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
          <!-- The text right after the tag: the pane keeps whitespace, so a line
               break here would indent every line. -->
          <div
            v-for="l in shown"
            :key="l.i"
            :class="{ 'log-error': l.error, 'rsm-log-line': l.error && ai?.available }"
          >{{ l.text || ' ' }}<button
              v-if="l.error && ai?.available"
              type="button"
              class="rsm-ask-ai"
              title="Ask the AI service what this error means"
              @click="asking = l.text"
            >✨ Ask AI</button>
          </div>
        </template>
        <div v-else-if="errorsOnly && logLines.length" class="log-muted">// no errors in this window</div>
        <div v-else class="log-muted">// waiting for log output…</div>
      </div>
    </div>

    <AskAiDialog v-if="asking" :server-id="id" :line="asking" :ai="ai" @close="asking = ''" />
    <AskAiDialog v-if="askingFile" :server-id="id" :path="askingFile" :ai="ai" @close="askingFile = ''" />

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
                <td class="text-end text-nowrap">
                  <button
                    v-if="ai?.available && isTextLog(f.path)"
                    class="btn btn-sm btn-outline-secondary me-1"
                    title="Ask the AI service what went wrong in this log"
                    @click="askingFile = f.path"
                  >✨ Ask AI</button>
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

/* A small button at the end of an error line; quiet until the line is hovered,
   always there on a touch screen. */
.rsm-ask-ai {
  margin-left: 0.75rem;
  padding: 0 0.4rem;
  font-size: 0.75rem;
  line-height: 1.4;
  border: 1px solid currentColor;
  border-radius: 0.25rem;
  background: transparent;
  color: inherit;
  opacity: 0.55;
  white-space: nowrap;
  /* not part of the log text when lines are copied */
  user-select: none;
}

.rsm-log-line:hover .rsm-ask-ai,
.rsm-ask-ai:focus-visible {
  opacity: 1;
}

@media (hover: none) {
  .rsm-ask-ai {
    opacity: 1;
  }
}
</style>
