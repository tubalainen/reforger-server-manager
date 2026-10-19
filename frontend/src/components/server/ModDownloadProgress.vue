<script setup>
import { computed } from 'vue'
import { downloadView } from '../../modDownload'

// The mod download of a starting server, as a progress bar (#235). With no known
// total the bar runs full width, striped, so it still shows that work is going on.
const props = defineProps({
  download: { type: Object, default: null }, // stats.mod_download
})
const v = computed(() => downloadView(props.download))
const width = computed(() => (v.value.percent == null ? 100 : v.value.percent))
</script>

<template>
  <div v-if="v.show">
    <div class="small fw-semibold mb-1">{{ v.label }}</div>
    <div
      class="progress"
      role="progressbar"
      :aria-label="v.label"
      :aria-valuenow="v.percent ?? undefined"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div
        class="progress-bar"
        :class="{
          'progress-bar-striped progress-bar-animated': v.animated,
          'bg-success': v.phase === 'loading',
          'opacity-50': v.percent == null && v.phase !== 'loading',
        }"
        :style="{ width: `${width}%` }"
      ></div>
    </div>
    <small v-if="v.detail" class="d-block text-secondary mt-1">{{ v.detail }}</small>
  </div>
</template>
