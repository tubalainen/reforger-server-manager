<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { isUnder, SUBNAV } from '../nav'

// Library and System each hold a few related pages (#189); this is the row of
// links between them, above whichever page is open.
const props = defineProps({ section: { type: String, required: true } })
const route = useRoute()
const items = computed(() => SUBNAV[props.section] || [])

// On a phone the row is wider than the screen and scrolls sideways; keep the page
// you are on in view instead of leaving it cut off at the edge.
const navEl = ref(null)
function revealActive() {
  nextTick(() => {
    navEl.value?.querySelector('.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  })
}
watch(() => route.path, revealActive)

// System is about this install, so it names the version it runs — and on a phone,
// where the rail has no room for it, this is where the version is found.
const version = ref(null)
onMounted(async () => {
  revealActive()
  if (props.section !== 'system') return
  try {
    version.value = await api('/api/version')
  } catch {
    /* ignore */
  }
})
</script>

<template>
  <div class="container">
    <nav
      ref="navEl"
      class="rsm-tabs nav nav-underline mb-4"
      :aria-label="section"
    >
      <router-link
        v-for="item in items"
        :key="item.to"
        :to="item.to"
        class="nav-link text-nowrap"
        :class="{ active: isUnder(route.path, item.to) }"
        :aria-current="isUnder(route.path, item.to) ? 'page' : undefined"
      >{{ item.label }}</router-link>
      <a
        v-if="version && version.version"
        :href="version.repo_url"
        target="_blank"
        rel="noopener"
        class="nav-link ms-auto text-nowrap small text-secondary"
        :title="'Open ' + version.name + ' on GitHub'"
      >v{{ version.version }} ↗</a>
    </nav>
  </div>
  <router-view />
</template>
