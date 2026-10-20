<script setup>
import { formatBytes } from '../format'

// A download's progress bar while it runs, and its error once it failed (#241:
// shared by System › Server files and the first-run setup screen).
defineProps({ job: { type: Object, default: null } })

const fmtBytes = (n) => formatBytes(n, { base: 1000, empty: '' })
</script>

<template>
  <div v-if="job && job.status === 'running'" class="mb-2">
    <div class="d-flex justify-content-between small mb-1">
      <span class="text-capitalize">{{ job.phase }}</span>
      <span>
        {{ job.percent.toFixed(1) }}%
        <template v-if="job.bytes_total">
          ({{ fmtBytes(job.bytes_done) }} / {{ fmtBytes(job.bytes_total) }})
        </template>
      </span>
    </div>
    <div class="progress" style="height: 0.75rem">
      <div
        class="progress-bar progress-bar-striped progress-bar-animated"
        :style="{ width: job.percent + '%' }"
      ></div>
    </div>
  </div>
  <div v-if="job && job.status === 'error'" class="alert alert-danger py-2 small mb-2">
    {{ job.error }}
  </div>
</template>
