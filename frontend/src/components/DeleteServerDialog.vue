<script setup>
import { computed, onMounted, reactive } from 'vue'
import { api } from '../api'
import { formatBytes } from '../format'

// Delete a server, offering to wipe its data from disk too rather than just drop
// the container and orphan the folder (#131 follow-up). Shared by the Servers
// overview and a server's own page (#189).
const props = defineProps({
  server: { type: Object, required: true }, // { id, name }
})
const emit = defineEmits(['close', 'deleted'])

const del = reactive({ data: null, purge: false, busy: false, error: '' })
const fmtBytes = (n) => formatBytes(n, { empty: 'empty' })

// What is on disk, only the targets that actually hold something.
const delItems = computed(() => (del.data?.items || []).filter((i) => i.files))
const delTotalBytes = computed(() =>
  (del.data?.items || []).reduce((sum, i) => sum + (i.size_bytes || 0), 0),
)

onMounted(() => {
  // Show what wiping would take with it; the delete works fine without this.
  api(`/api/instances/${props.server.id}/data`)
    .then((d) => { del.data = d })
    .catch(() => { del.data = { items: [] } })
})

async function confirmDelete() {
  del.busy = true
  del.error = ''
  try {
    const q = del.purge ? '?purge_data=true' : ''
    await api(`/api/instances/${props.server.id}${q}`, { method: 'DELETE' })
    emit('deleted')
  } catch (e) {
    del.error = e.message
  } finally {
    del.busy = false
  }
}
</script>

<template>
  <div class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">Delete "{{ server.name }}"?</h5>
          <button type="button" class="btn-close" aria-label="Close" @click="emit('close')"></button>
        </div>
        <div class="modal-body">
          <div v-if="del.error" class="alert alert-danger py-2 small">{{ del.error }}</div>

          <p class="mb-3">
            This server's container is removed and it disappears from the list.
          </p>

          <div class="form-check mb-2">
            <input id="purgeData" v-model="del.purge" class="form-check-input" type="checkbox" />
            <label for="purgeData" class="form-check-label">
              Also delete all stored data from disk
              <small class="text-secondary d-block">
                Baked mods, saved game, logs, configs — and this server's saved game
                backups. Otherwise they are left on the host and can no longer be
                reached from the manager.
              </small>
            </label>
          </div>

          <!-- What is actually on disk, so the choice is informed (#79 data). -->
          <div v-if="del.data === null" class="small text-secondary">Checking disk usage…</div>
          <template v-else>
            <ul v-if="delItems.length" class="small mb-2">
              <li v-for="item in delItems" :key="item.target">
                <span class="text-capitalize">{{ item.target }}</span>
                — {{ fmtBytes(item.size_bytes) }} ({{ item.files }} file(s))
              </li>
            </ul>
            <p v-else class="small text-secondary mb-2">Nothing is stored on disk yet.</p>

            <div v-if="del.purge && delTotalBytes" class="alert alert-danger py-2 small mb-0">
              This permanently erases {{ fmtBytes(delTotalBytes) }} of data, including the
              saved game and every backup of it — the persistent world is gone for good
              and cannot be recovered. Download the backups you want to keep first.
            </div>
          </template>
        </div>
        <div class="modal-footer">
          <button class="btn btn-outline-secondary" @click="emit('close')">Cancel</button>
          <button class="btn btn-danger" :disabled="del.busy" @click="confirmDelete">
            {{ del.busy ? 'Deleting…' : (del.purge ? 'Delete server & data' : 'Delete server') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
