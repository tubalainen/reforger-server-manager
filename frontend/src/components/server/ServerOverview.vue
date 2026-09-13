<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api'
import { formatBytes, formatUptime } from '../../format'
import { isErrorLine } from '../../log'
import { serverSeries, tabParams } from '../../server'
import Sparkline from '../Sparkline.vue'

// The first thing a server's page shows (#189): its live figures, the last few
// console lines, and how it is looked after — schedule, crash and reboot behaviour,
// the latest backup. Everything else is a tab away.
const props = defineProps({
  inst: { type: Object, required: true },
  stats: { type: Object, default: null },
  view: { type: Object, required: true }, // serverStatus() of this server
  logLines: { type: Array, required: true },
  history: { type: Array, default: () => [] },
  maxPlayers: { type: Number, default: null },
})
const emit = defineEmits(['action'])

const running = computed(() => props.inst.status === 'running')
const online = computed(() => running.value && !props.view.starting)
const fmtMem = (n) => formatBytes(n, { empty: '—' })

const series = (key) => serverSeries(props.history, props.inst.id, key)
const tiles = computed(() => {
  const s = props.stats || {}
  return [
    {
      key: 'players',
      label: 'Players',
      value: s.players ?? '—',
      unit: props.maxPlayers != null ? ` / ${props.maxPlayers}` : '',
      values: series('players'),
    },
    { key: 'fps', label: 'Server FPS', value: s.server_fps ?? '—', unit: '', values: series('server_fps') },
    {
      key: 'cpu',
      label: 'CPU · whole machine',
      value: s.cpu_percent ?? '—',
      unit: s.cpu_percent != null ? '%' : '',
      values: series('cpu_percent'),
    },
    {
      key: 'mem',
      label: 'Memory',
      value: fmtMem(s.mem_bytes),
      unit: s.mem_limit_bytes ? ` / ${fmtMem(s.mem_limit_bytes)}` : '',
      values: series('mem_bytes'),
      // The server's own reading of its heap, next to what the container uses (#88).
      title: s.server_mem_kb ? `The server reports ${fmtMem(s.server_mem_kb * 1024)} for itself` : '',
    },
  ]
})

const recentLines = computed(() => props.logLines.slice(-6))

// The newest backup, for "when was the world last copied".
const lastBackup = ref(undefined) // undefined while loading, null when there is none
onMounted(async () => {
  try {
    const info = await api(`/api/instances/${props.inst.id}/backups`)
    lastBackup.value = info.backups?.[0] || null
  } catch {
    lastBackup.value = null
  }
})

const restartTimes = computed(() => (props.inst.restart_times || []).join(', ') || 'none')
</script>

<template>
  <div class="d-grid gap-3">
    <!-- Live figures while it runs; one plain sentence when it doesn't -->
    <div v-if="running" class="rsm-tiles">
      <div v-for="t in tiles" :key="t.key" class="rsm-tile" :title="t.title">
        <div class="small text-secondary">{{ t.label }}</div>
        <div class="rsm-tile-value">{{ t.value }}<span class="rsm-tile-unit">{{ t.unit }}</span></div>
        <Sparkline class="text-primary" :values="t.values" :label="`${t.label}, last hour`" />
      </div>
    </div>

    <div v-if="running && !online" class="card">
      <div class="card-body d-flex align-items-start gap-2 py-2">
        <span class="rsm-dot rsm-dot-text bg-warning" aria-hidden="true"></span>
        <span>
          <strong>{{ view.long }}</strong>
          <span v-if="view.note" class="text-secondary"> · {{ view.note }}</span>.
          Players can join once it shows online.
        </span>
      </div>
    </div>
    <div v-else-if="!running" class="card">
      <div class="card-body d-flex flex-wrap align-items-center gap-2 py-2">
        <span class="rsm-dot bg-secondary" aria-hidden="true"></span>
        <span class="me-auto">
          <strong>Not running.</strong>
          <span class="text-secondary"> Its world, backups and settings are kept.</span>
        </span>
        <button
          class="btn btn-sm btn-success"
          :disabled="!inst.server_files_ready"
          @click="emit('action', 'start')"
        >Start server</button>
      </div>
    </div>

    <div class="row g-3">
      <div class="col-lg-7">
        <div class="card h-100">
          <div class="card-header d-flex justify-content-between align-items-center py-2">
            <span class="fw-semibold small">Latest console</span>
            <router-link
              class="small text-decoration-none"
              :to="{ name: 'instance-detail', params: tabParams(inst.id, 'console') }"
            >Open console</router-link>
          </div>
          <div class="rsm-log rsm-log-snippet rounded-bottom p-2">
            <template v-if="recentLines.length">
              <div v-for="(l, i) in recentLines" :key="i" :class="{ 'log-error': isErrorLine(l) }">{{ l || ' ' }}</div>
            </template>
            <div v-else class="log-muted">// waiting for log output…</div>
          </div>
        </div>
      </div>

      <div class="col-lg-5">
        <div class="card h-100">
          <div class="card-header d-flex justify-content-between align-items-center py-2">
            <span class="fw-semibold small">Schedule &amp; saves</span>
            <router-link
              class="small text-decoration-none"
              :to="{ name: 'instance-detail', params: tabParams(inst.id, 'settings') }"
            >Change</router-link>
          </div>
          <dl class="rsm-facts small mb-0">
            <div v-if="running"><dt>Up for</dt><dd class="rsm-num">{{ formatUptime(stats?.uptime_seconds) }}</dd></div>
            <div>
              <dt>Next restart</dt>
              <dd>{{ inst.next_restart ? `${inst.next_restart} (server time)` : '—' }}</dd>
            </div>
            <div><dt>Daily restarts</dt><dd>{{ restartTimes }}</dd></div>
            <div><dt>After a crash</dt><dd>{{ inst.auto_restart ? 'Restarts' : 'Stays down' }}</dd></div>
            <div><dt>After a host reboot</dt><dd>{{ inst.auto_start ? 'Starts again' : 'Stays down' }}</dd></div>
            <div>
              <dt>Last save backup</dt>
              <dd>
                <template v-if="lastBackup === undefined">…</template>
                <template v-else-if="lastBackup">{{ lastBackup.created_display || lastBackup.created_at }}</template>
                <router-link
                  v-else
                  :to="{ name: 'instance-detail', params: tabParams(inst.id, 'saves') }"
                >none yet</router-link>
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rsm-log-snippet {
  min-height: 9rem;
  max-height: 14rem;
}

.rsm-facts {
  padding: 0.35rem 1rem 0.5rem;
}

.rsm-facts > div {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.4rem 0;
  border-bottom: 1px dashed var(--bs-border-color);
}

.rsm-facts > div:last-child {
  border-bottom: 0;
}

.rsm-facts dt {
  font-weight: 400;
  color: var(--bs-secondary-color);
}

.rsm-facts dd {
  margin: 0;
  text-align: right;
}
</style>
