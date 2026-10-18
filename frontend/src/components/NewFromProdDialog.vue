<script setup>
// "+ New → Based on a Prod …" (#223): copy a released template or mod list into
// Test and open the copy. Releasing that copy later defaults to "next version
// of" the one picked here.
import { computed, reactive } from 'vue'
import { api } from '../api'
import { versionLabel, versionOptions } from '../stages'

const props = defineProps({
  prods: { type: Array, required: true },
  base: { type: String, required: true },   // /api/templates | /api/mod-templates
  noun: { type: String, default: 'template' },
})
const emit = defineEmits(['close', 'created'])

const form = reactive({ id: props.prods[0]?.id ?? null, version: null, busy: false, error: '' })
// Any released version can be the starting point, the latest by default.
const versions = computed(() => versionOptions(props.prods.find((p) => p.id === form.id)))

async function submit() {
  form.busy = true
  form.error = ''
  try {
    const body = form.version != null ? { version: form.version } : undefined
    const created = await api(`${props.base}/${form.id}/copy`, { method: 'POST', body })
    emit('created', created)
  } catch (e) {
    form.error = e.message
  } finally {
    form.busy = false
  }
}
</script>

<template>
  <div class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">New {{ noun }} based on a Prod one</h5>
          <button type="button" class="btn-close" aria-label="Close" @click="emit('close')"></button>
        </div>
        <div class="modal-body">
          <div v-if="form.error" class="alert alert-danger py-2 small">{{ form.error }}</div>
          <p v-if="!prods.length" class="text-secondary mb-0">
            Nothing is in Prod yet. Release a {{ noun }} first, or start a blank one.
          </p>
          <template v-else>
            <p class="small text-secondary">
              A copy lands in Test, where you can change it freely. When it has been
              through Dev, release it as the next version of the one you pick here.
            </p>
            <select
              v-model.number="form.id"
              class="form-select"
              aria-label="Prod item to copy"
              @change="form.version = null"
            >
              <option v-for="p in prods" :key="p.id" :value="p.id">
                {{ p.name }} ({{ versionLabel(p) }})
              </option>
            </select>
            <select
              v-if="versions.length > 2"
              v-model="form.version"
              class="form-select mt-2"
              aria-label="Version to copy"
            >
              <option v-for="o in versions" :key="String(o.value)" :value="o.value">{{ o.label }}</option>
            </select>
          </template>
        </div>
        <div class="modal-footer">
          <button class="btn btn-outline-secondary" @click="emit('close')">Cancel</button>
          <button class="btn btn-primary" :disabled="form.busy || !prods.length" @click="submit">
            {{ form.busy ? 'Copying…' : 'Copy to Test' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
