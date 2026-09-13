<script setup>
import { computed } from 'vue'
import { sparkline } from '../overview'

// A small trend line under a totals figure on the Servers overview (#189). Drawn in
// the text colour it is given, so it follows the theme. Nothing is drawn until
// there are two readings to join.
const props = defineProps({
  values: { type: Array, required: true },
  label: { type: String, default: '' },
})

const WIDTH = 120
const HEIGHT = 28
const geo = computed(() => sparkline(props.values, WIDTH, HEIGHT))
</script>

<template>
  <svg
    v-if="geo"
    class="rsm-spark"
    :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
    preserveAspectRatio="none"
    role="img"
    :aria-label="label"
  >
    <path :d="geo.area" fill="currentColor" fill-opacity="0.14" stroke="none" />
    <path
      :d="geo.line"
      fill="none"
      stroke="currentColor"
      stroke-width="1.5"
      stroke-linejoin="round"
      vector-effect="non-scaling-stroke"
    />
  </svg>
  <div v-else class="rsm-spark rsm-spark-empty" aria-hidden="true"></div>
</template>

<style scoped>
.rsm-spark {
  display: block;
  width: 100%;
  height: 1.75rem;
}
</style>
