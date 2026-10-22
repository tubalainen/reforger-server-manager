<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api'

// What an error line in the Console means, asked of the AI service set up in .env
// (#254). The manager builds the question from its own copy of the log around
// this line, with IP addresses and player ids removed; "What was sent" shows it.
const props = defineProps({
  serverId: { type: [String, Number], required: true },
  line: { type: String, required: true },
  ai: { type: Object, required: true }, // /api/system/ai: {provider, model}
})
const emit = defineEmits(['close'])

const busy = ref(true)
const reply = ref('')
const sent = ref('')
const model = ref('')
const error = ref('')

async function ask() {
  busy.value = true
  error.value = ''
  try {
    const res = await api(`/api/instances/${props.serverId}/logs/explain`, {
      method: 'POST',
      body: { line: props.line },
    })
    reply.value = res.reply
    sent.value = res.sent
    model.value = res.model
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
onMounted(ask)
</script>

<template>
  <div class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">✨ What does this error mean?</h5>
          <button type="button" class="btn-close" aria-label="Close" @click="emit('close')"></button>
        </div>
        <div class="modal-body">
          <pre class="rsm-ai-line small mb-3">{{ line }}</pre>
          <div v-if="busy" class="text-secondary small">
            <span class="spinner-border spinner-border-sm me-2"></span>
            Asking {{ ai.provider === 'ollama' ? 'Ollama' : 'the AI service' }}
            <template v-if="ai.model"> ({{ ai.model }})</template>… a local model can take a minute.
          </div>
          <div v-else-if="error" class="alert alert-warning small mb-0">
            {{ error }}
            <div class="mt-2">
              <button class="btn btn-sm btn-outline-secondary" @click="ask">Try again</button>
            </div>
          </div>
          <template v-else>
            <div class="rsm-ai-reply">{{ reply }}</div>
            <p class="small text-secondary mt-3 mb-2">
              Answered by {{ model || 'the AI service' }}. An AI can be wrong: treat this as a
              lead, not a verdict.
            </p>
            <details class="small">
              <summary class="text-secondary">What was sent</summary>
              <p class="text-secondary mt-2 mb-1">
                The lines before the error, from this server's log, and the template's mod
                names. IP addresses, Steam ids, BattlEye GUIDs and identity ids were removed.
              </p>
              <pre class="rsm-ai-sent">{{ sent }}</pre>
            </details>
          </template>
        </div>
        <div class="modal-footer">
          <button class="btn btn-outline-secondary" @click="emit('close')">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rsm-ai-line,
.rsm-ai-sent {
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--bs-tertiary-bg);
  border-radius: 0.375rem;
  padding: 0.5rem 0.75rem;
}

.rsm-ai-line {
  color: var(--bs-danger-text-emphasis);
}

.rsm-ai-sent {
  max-height: 20rem;
  overflow: auto;
}

.rsm-ai-reply {
  white-space: pre-wrap;
}
</style>
