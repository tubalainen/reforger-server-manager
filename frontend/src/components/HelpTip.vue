<script setup>
import { nextTick, onUnmounted, ref, useId } from 'vue'
import { placePopover } from '../help'

// A "?" beside something, opening the longer explanation of it (#189). The page
// shows a short hint; this holds the full text that used to sit there on every
// visit. Bootstrap's popover needs its JavaScript, which the app does not ship
// (#175), so this is its own small one.
defineProps({
  // What the "?" explains, for screen readers: "About saved game backups".
  label: { type: String, required: true },
})

const open = ref(false)
const button = ref(null)
const panel = ref(null)
const pos = ref({ left: 0, top: 0 })
const panelId = `help-${useId()}`

async function place() {
  await nextTick()
  if (!button.value || !panel.value) return
  const a = button.value.getBoundingClientRect()
  const p = panel.value.getBoundingClientRect()
  pos.value = placePopover(a, p, { width: window.innerWidth, height: window.innerHeight })
}

function onOutside(e) {
  if (panel.value?.contains(e.target) || button.value?.contains(e.target)) return
  close()
}
function onKey(e) {
  if (e.key === 'Escape') {
    close()
    button.value?.focus()
  }
}

function listen(on) {
  const method = on ? 'addEventListener' : 'removeEventListener'
  document[method]('mousedown', onOutside)
  document[method]('keydown', onKey)
  // A fixed popover would drift away from its button: close it instead.
  window[method]('scroll', close, true)
  window[method]('resize', close)
}

function toggle() {
  if (open.value) return close()
  open.value = true
  listen(true)
  place()
}
function close() {
  if (!open.value) return
  open.value = false
  listen(false)
}

onUnmounted(() => listen(false))
</script>

<template>
  <button
    ref="button"
    type="button"
    class="rsm-help-btn"
    :aria-label="label"
    :aria-expanded="open"
    :aria-controls="panelId"
    @click="toggle"
  >?</button>
  <Teleport to="body">
    <div
      v-if="open"
      :id="panelId"
      ref="panel"
      class="rsm-help-panel card shadow"
      role="note"
      :style="{ left: `${pos.left}px`, top: `${pos.top}px` }"
    >
      <div class="card-body py-2 px-3 small">
        <slot />
      </div>
    </div>
  </Teleport>
</template>

<style>
/* Not scoped: the panel is teleported to <body>, outside this component's tree. */
.rsm-help-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.15rem;
  height: 1.15rem;
  padding: 0;
  margin-left: 0.25rem;
  border: 1px solid var(--bs-border-color);
  border-radius: 50%;
  background: transparent;
  color: var(--bs-secondary-color);
  font-size: 0.7rem;
  font-weight: 600;
  line-height: 1;
  vertical-align: 0.1em;
}

.rsm-help-btn:hover,
.rsm-help-btn[aria-expanded='true'] {
  color: var(--bs-emphasis-color);
  border-color: var(--bs-secondary-color);
}

.rsm-help-panel {
  position: fixed;
  z-index: 1070;
  width: min(24rem, calc(100vw - 1rem));
  font-weight: 400;
  text-align: left;
  white-space: normal;
}

.rsm-help-panel p:last-child {
  margin-bottom: 0;
}
</style>
