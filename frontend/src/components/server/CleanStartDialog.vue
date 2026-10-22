<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api'
import { formatBytes } from '../../format'

// Start after a template switch. The mods, saved game and logs on disk came from
// the template this server last ran, so before it starts on the new one, ask
// what to clear. Nothing is ticked: a plain Start keeps everything, as before.
const props = defineProps({
  server: { type: Object, required: true }, // a server view or summary row
})
const emit = defineEmits(['close', 'started'])

const KINDS = [
  { target: 'mods', label: 'Downloaded & baked mods',
    hint: 'The old template\'s mods. Cleared, the server downloads only the new template\'s mods.' },
  { target: 'saves', label: 'Saved game data',
    hint: 'The world the old template\'s scenario built. Cleared, the new template starts a fresh one.' },
  { target: 'logs', label: 'Logs & crash reports',
    hint: 'Past sessions\' logs. Only history is lost.' },
]

const items = ref(null)
const loadError = ref('')
const picked = ref([])
const backupFirst = ref(true)
const busy = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    const data = await api(`/api/instances/${props.server.id}/data`)
    items.value = KINDS.map((k) => ({ ...k, ...(data.items.find((i) => i.target === k.target) || {}) }))
  } catch (e) {
    loadError.value = e.message
    items.value = KINDS.map((k) => ({ ...k, files: null }))
  }
})

// Explicit toggles, as in StoredData: v-model on an array loses a second tick
// made before the re-render.
function pick(target, on) {
  const next = new Set(picked.value)
  if (on) next.add(target)
  else next.delete(target)
  picked.value = KINDS.map((k) => k.target).filter((t) => next.has(t))
}
const clearingSaves = computed(() => picked.value.includes('saves'))

async function start() {
  busy.value = true
  error.value = ''
  try {
    const body = picked.value.length
      ? { clear: picked.value, backup_first: clearingSaves.value && backupFirst.value }
      : undefined
    const res = await api(`/api/instances/${props.server.id}/start`, { method: 'POST', body })
    emit('started', res)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">Start "{{ server.name }}" on its new template?</h5>
          <button type="button" class="btn-close" aria-label="Close" @click="emit('close')"></button>
        </div>
        <div class="modal-body">
          <p class="small">
            This server last ran
            <strong>{{ server.previous_template_name || 'another template' }}</strong>
            and now runs <strong>{{ server.template_name || 'a new template' }}</strong>.
            What is on disk came from the old one. Clear any of it before starting?
          </p>
          <div v-if="loadError" class="alert alert-warning py-2 small">
            Could not read what is on disk: {{ loadError }}
          </div>
          <p v-if="!items" class="small text-secondary">Reading what is on disk…</p>
          <div v-else class="list-group list-group-flush mb-2">
            <div v-for="item in items" :key="item.target" class="list-group-item d-flex gap-3 px-0">
              <input
                :id="`clean-${item.target}`"
                class="form-check-input mt-1 flex-shrink-0"
                type="checkbox"
                :checked="picked.includes(item.target)"
                :disabled="item.files === 0 || busy"
                @change="pick(item.target, $event.target.checked)"
              />
              <label :for="`clean-${item.target}`" class="flex-grow-1 small">
                <span class="fw-semibold">{{ item.label }}</span>
                <span v-if="item.files != null" class="badge text-bg-secondary ms-2">
                  {{ item.files ? formatBytes(item.size_bytes) : 'empty' }}
                </span>
                <span v-if="item.target === 'saves' && item.files" class="badge text-bg-danger ms-1">
                  destructive
                </span>
                <span class="d-block text-secondary">{{ item.hint }}</span>
              </label>
            </div>
          </div>
          <template v-if="clearingSaves">
            <div class="alert alert-danger py-2 small mb-2">
              The saved world is deleted for good: the new template starts from scratch.
            </div>
            <div class="form-check small">
              <input id="clean-backup" v-model="backupFirst" class="form-check-input" type="checkbox" />
              <label class="form-check-label" for="clean-backup">
                Back up the saved game first (it lands on the Saves tab)
              </label>
            </div>
          </template>
          <p v-if="!picked.length" class="small text-secondary mb-0">
            Nothing ticked: it starts with everything kept, and the new template's mods
            download next to the old ones.
          </p>
          <div v-if="error" class="alert alert-danger py-2 small mt-2 mb-0">{{ error }}</div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-outline-secondary" :disabled="busy" @click="emit('close')">Cancel</button>
          <button
            class="btn"
            :class="clearingSaves ? 'btn-danger' : 'btn-primary'"
            :disabled="busy || !items"
            @click="start"
          >
            {{ busy ? 'Starting…' : picked.length ? 'Clear and start' : 'Start, keep everything' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
