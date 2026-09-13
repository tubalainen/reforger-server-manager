<script setup>
import SaveBackups from '../SaveBackups.vue'
import StoredData from '../StoredData.vue'

// The world and the copies of it (#189): saved game backups first, then the saved
// game on disk with the option to clear it — the copy comes before the thing that
// deletes (#179).
defineProps({
  inst: { type: Object, required: true },
})
const emit = defineEmits(['changed'])
</script>

<template>
  <div class="d-grid gap-3">
    <SaveBackups
      :id="inst.id"
      :running="inst.status === 'running'"
      @changed="emit('changed')"
    />
    <StoredData
      :id="inst.id"
      :running="inst.status === 'running'"
      :targets="['saves']"
      :server-name="inst.name"
      title="Saved game on disk"
      @changed="emit('changed')"
    />
  </div>
</template>
