<script setup>
import { computed, ref } from 'vue'
import { formatBytes } from '../../format'
import { filterServers, tabParams } from '../../server'
import { serverStatus } from '../../status'

// Every server, beside the one you have open (#189). One click switches server and
// keeps you on the same tab, so comparing two consoles is two clicks, not four.
const props = defineProps({
  summary: { type: Object, default: null },
  currentId: { type: [String, Number], required: true },
  tab: { type: String, required: true },
  updates: { type: Number, default: 0 },
})

const query = ref('')
const servers = computed(() => props.summary?.servers || [])
const shown = computed(() => filterServers(servers.value, query.value))

function rowView(s) {
  return serverStatus(s.status, s.server_state)
}
function dotClass(s) {
  return rowView(s).cls.replace('text-bg-', 'bg-')
}
// Players when it is up; otherwise the state, which is the more useful word.
function rightText(s) {
  const v = rowView(s)
  if (s.status === 'running' && !v.starting) {
    return `${s.players ?? '—'}${s.max_players != null ? `/${s.max_players}` : ''}`
  }
  return v.label
}
</script>

<template>
  <aside class="rsm-server-list card" aria-label="Servers">
    <div class="card-header d-flex align-items-center gap-2 py-2">
      <router-link :to="{ name: 'instances' }" class="fw-semibold text-decoration-none">
        All servers
      </router-link>
      <span v-if="summary" class="small text-secondary ms-auto">
        {{ summary.running }} of {{ summary.total }} online
      </span>
    </div>

    <div v-if="servers.length > 4" class="px-2 pt-2">
      <input
        v-model="query"
        type="search"
        class="form-control form-control-sm"
        placeholder="Filter servers"
        aria-label="Filter servers"
      />
    </div>

    <nav class="rsm-server-rows py-1">
      <router-link
        v-for="s in shown"
        :key="s.id"
        :to="{ name: 'instance-detail', params: tabParams(s.id, tab) }"
        class="rsm-server-row text-decoration-none"
        :class="{ active: String(s.id) === String(currentId) }"
        :aria-current="String(s.id) === String(currentId) ? 'page' : undefined"
      >
        <span class="rsm-dot" :class="dotClass(s)" aria-hidden="true"></span>
        <span class="rsm-server-row-name">
          <span class="d-block text-truncate">{{ s.name }}</span>
          <small class="d-block text-truncate text-secondary">{{ s.scenario_name || s.template_name || '—' }}</small>
        </span>
        <small class="rsm-num text-secondary">{{ rightText(s) }}</small>
      </router-link>
      <p v-if="summary && !shown.length" class="small text-secondary px-3 py-2 mb-0">
        No server matches "{{ query }}".
      </p>
    </nav>

    <div v-if="summary" class="card-footer small d-grid gap-1">
      <div class="text-secondary rsm-num" title="This host, across every running server">
        {{ summary.players_total }} players<template v-if="summary.cpu_percent != null">
          · CPU {{ summary.cpu_percent }}%</template><template v-if="summary.mem_bytes">
          · {{ formatBytes(summary.mem_bytes) }}</template>
      </div>
      <router-link v-if="updates" :to="{ name: 'server-files' }" class="text-decoration-none">
        New server release · update server files
      </router-link>
    </div>
  </aside>
</template>

<style scoped>
.rsm-server-list {
  position: sticky;
  top: 1.5rem;
  max-height: calc(100vh - 3rem);
  overflow: hidden;
}

.rsm-server-rows {
  overflow-y: auto;
  flex: 1 1 auto;
  min-height: 0;
}

.rsm-server-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.75rem 0.5rem 0.85rem;
  color: var(--bs-body-color);
  border-left: 2px solid transparent;
}

.rsm-server-row:hover {
  background: var(--bs-tertiary-bg);
}

.rsm-server-row.active {
  background: var(--bs-secondary-bg);
  border-left-color: var(--bs-primary);
}

.rsm-server-row-name {
  min-width: 0;
}
</style>
