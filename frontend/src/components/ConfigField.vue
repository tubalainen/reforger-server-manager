<script setup>
import { computed, useId } from 'vue'
import HelpTip from './HelpTip.vue'
import { isChanged } from '../configFields'

// One setting from the field catalog (configFields.js): its control, the
// config.json key it writes, a "?" with what it does, and — once it differs from
// the engine default — a dot and a way back to that default.
const props = defineProps({
  field: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})
const value = defineModel({ type: [Boolean, Number, String], default: null })
const id = `cfg-${useId()}`
const changed = computed(() => isChanged(props.field, value.value))

function reset() {
  value.value = props.field.default
}

// "1–128", "≥ 50", "-1–1000 s" — the range the input accepts, from the model.
const range = computed(() => {
  const { min, max, unit } = props.field
  let text = ''
  if (min != null && max != null) text = `${min}–${max}`
  else if (min != null) text = `≥ ${min}`
  else if (max != null) text = `≤ ${max}`
  return [text, unit].filter(Boolean).join(' ')
})
</script>

<template>
  <div class="rsm-config-field">
    <div v-if="field.kind === 'bool'" class="form-check mb-0">
      <input :id="id" v-model="value" class="form-check-input" type="checkbox" :disabled="disabled" />
      <label :for="id" class="form-check-label">{{ field.label }}</label>
      <HelpTip :label="`About ${field.label}`">{{ field.help }}</HelpTip>
    </div>
    <template v-else>
      <label :for="id" class="form-label small mb-1">
        {{ field.label }}
        <small v-if="range" class="text-secondary">({{ range }})</small>
        <HelpTip :label="`About ${field.label}`">{{ field.help }}</HelpTip>
      </label>
      <select v-if="field.kind === 'select'" :id="id" v-model="value" class="form-select form-select-sm"
        :disabled="disabled">
        <option v-for="opt in field.options" :key="opt" :value="opt">{{ opt }}</option>
      </select>
      <input v-else-if="field.kind === 'int'" :id="id" v-model.number="value" type="number"
        class="form-control form-control-sm" :min="field.min" :max="field.max" :disabled="disabled" />
      <input v-else :id="id" v-model="value" class="form-control form-control-sm" :disabled="disabled" />
    </template>
    <div class="rsm-config-key small" :class="{ 'ps-4': field.kind === 'bool' }">
      <code :title="field.path">{{ field.key }}</code>
      <template v-if="changed">
        <span class="text-warning-emphasis ms-2" title="Changed from the default">● changed</span>
        <button type="button" class="btn btn-link btn-sm p-0 ms-1 align-baseline" :disabled="disabled"
          :title="`Back to the default: ${field.default}`" @click="reset">reset</button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.rsm-config-key code {
  font-size: 0.75rem;
  color: var(--bs-secondary-color);
}
.rsm-config-key .btn-link {
  font-size: 0.75rem;
}
</style>
