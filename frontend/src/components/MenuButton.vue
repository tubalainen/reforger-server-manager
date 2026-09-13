<script setup>
import { onUnmounted, ref, useId } from 'vue'

// A button that opens a short menu of actions. Bootstrap's dropdown needs its
// JavaScript, which the app does not ship (#175), so Vue opens and closes it.
// The menu stays in the DOM while closed (v-show via .show) so a file input inside
// it — Import JSON — still receives its change event after the menu closes.
defineProps({
  label: { type: String, required: true },
  // For an icon-only button like "⋯": what screen readers announce.
  ariaLabel: { type: String, default: '' },
  buttonClass: { type: String, default: 'btn btn-sm btn-outline-secondary' },
  align: { type: String, default: 'end' }, // start | end
})

const open = ref(false)
const root = ref(null)
const menuId = `menu-${useId()}`

function onOutside(e) {
  if (root.value && !root.value.contains(e.target)) close()
}
function onKey(e) {
  if (e.key === 'Escape') close()
}
function toggle() {
  if (open.value) return close()
  open.value = true
  document.addEventListener('mousedown', onOutside)
  document.addEventListener('keydown', onKey)
}
function close() {
  open.value = false
  document.removeEventListener('mousedown', onOutside)
  document.removeEventListener('keydown', onKey)
}
onUnmounted(close)
</script>

<template>
  <div ref="root" class="dropdown d-inline-block">
    <button
      type="button"
      :class="[buttonClass, 'dropdown-toggle']"
      :aria-label="ariaLabel || undefined"
      :aria-expanded="open"
      :aria-controls="menuId"
      @click="toggle"
    >{{ label }}</button>
    <!-- A click on any item runs it and closes the menu. -->
    <ul
      :id="menuId"
      class="dropdown-menu"
      :class="{ show: open, 'dropdown-menu-end': align === 'end' }"
      @click="close"
    >
      <slot />
    </ul>
  </div>
</template>

<style scoped>
.dropdown-menu.show {
  /* Bootstrap positions an open menu with Popper; without it, place it under the
     button ourselves. */
  position: absolute;
  top: 100%;
  margin-top: 0.25rem;
}

.dropdown-menu-end.show {
  right: 0;
  left: auto;
}
</style>
