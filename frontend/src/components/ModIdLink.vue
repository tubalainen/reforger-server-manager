<script setup>
// A mod id that opens the mod's Workshop page in a new tab (#221), so the
// description, dependencies and changelog are one click away from any mod list.
// An id that is not a Workshop asset id is shown as plain text.
import { computed } from 'vue'
import { workshopUrl } from '../mods'

const props = defineProps({
  modId: { type: String, required: true },
  name: { type: String, default: '' },
})

const href = computed(() => workshopUrl(props.modId))
</script>

<template>
  <!-- draggable="false": in the wizard's draggable mod rows a drag that starts
       on the id still moves the row instead of dragging the link away -->
  <a
    v-if="href"
    :href="href"
    target="_blank"
    rel="noopener noreferrer"
    draggable="false"
    class="rsm-mod-id"
    :title="`Open ${name || modId} on the Arma Reforger Workshop (new tab)`"
  >{{ modId }}</a>
  <span v-else>{{ modId }}</span>
</template>

<style scoped>
/* Same colour as the id text around it (secondary, in both themes); the
   underline on hover is what shows it is a link. */
.rsm-mod-id {
  color: inherit;
  text-decoration: none;
}
.rsm-mod-id:hover,
.rsm-mod-id:focus-visible {
  text-decoration: underline;
}
</style>
