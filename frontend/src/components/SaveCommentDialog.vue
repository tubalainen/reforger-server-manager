<script setup>
// The optional change comment a Dev save asks for (#223). It is written into the
// change log next to what the save changed; a save that changed nothing writes
// neither.
import { onMounted, ref } from 'vue'

defineProps({
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['save', 'cancel'])

const comment = ref('')
const input = ref(null)
onMounted(() => input.value?.focus())
</script>

<template>
  <div class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">What changed?</h5>
          <button type="button" class="btn-close" aria-label="Close" @click="emit('cancel')"></button>
        </div>
        <div class="modal-body">
          <p class="small text-secondary">
            This is in Dev, so the change log keeps a comment with each save.
            It's optional, and only kept when something changed.
          </p>
          <textarea
            ref="input"
            v-model="comment"
            class="form-control"
            rows="3"
            maxlength="500"
            placeholder="e.g. raised the player cap for Friday's op"
            aria-label="Change comment"
            @keydown.ctrl.enter="emit('save', comment)"
          ></textarea>
        </div>
        <div class="modal-footer">
          <button class="btn btn-outline-secondary" @click="emit('cancel')">Cancel</button>
          <button class="btn btn-primary" :disabled="busy" @click="emit('save', comment)">
            {{ busy ? 'Saving…' : 'Save' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
