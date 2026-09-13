<script setup>
import { useId } from 'vue'

// A card whose body folds away under its header (#189). The template wizard's
// optional groups — advanced settings, launch parameters, player access, the mission
// header — used to be "▸ Show …" links; as cards they line up with the rest of the
// app, and the header says what is set while the body is folded.
const open = defineModel({ type: Boolean, default: false })
defineProps({
  title: { type: String, required: true },
  // Shown at the right of the header, e.g. "3 overrides". Collapsed is the default,
  // so this is how something set inside is not forgotten (#154, #162).
  summary: { type: String, default: '' },
  summaryClass: { type: String, default: 'text-secondary' },
})
const bodyId = `collapsible-${useId()}`
</script>

<template>
  <section class="card rsm-collapsible">
    <button
      type="button"
      class="card-header rsm-collapsible-head"
      :class="{ 'border-bottom-0': !open }"
      :aria-expanded="open"
      :aria-controls="bodyId"
      @click="open = !open"
    >
      <span class="rsm-chevron" :class="{ open }" aria-hidden="true"></span>
      <span class="fw-semibold small">{{ title }}</span>
      <span v-if="summary" class="small ms-auto text-end" :class="summaryClass">{{ summary }}</span>
    </button>
    <div v-show="open" :id="bodyId" class="card-body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.rsm-collapsible-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  width: 100%;
  text-align: left;
  border-left: 0;
  border-right: 0;
  border-top: 0;
  color: var(--bs-body-color);
}

.rsm-collapsible-head:hover {
  background-color: var(--bs-secondary-bg);
}

.rsm-chevron {
  width: 0.5rem;
  height: 0.5rem;
  flex: none;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: rotate(-45deg);
  transition: transform 0.15s;
  opacity: 0.7;
}

.rsm-chevron.open {
  transform: rotate(45deg);
}

@media (prefers-reduced-motion: reduce) {
  .rsm-chevron {
    transition: none;
  }
}
</style>
