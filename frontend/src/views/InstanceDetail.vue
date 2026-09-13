<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import ServerList from '../components/server/ServerList.vue'
import ServerPane from '../components/server/ServerPane.vue'
import { normalizeTab, tabParams } from '../server'

// A server's page (#189): the list of every server on the left, the open server on
// the right. The summary behind the list also carries each server's player limit
// and the sparkline history, so it is fetched once here for both halves.
const props = defineProps({
  id: { type: [String, Number], required: true },
  tab: { type: String, default: '' },
})
const router = useRouter()

const currentTab = computed(() => normalizeTab(props.tab))

const summary = ref(null)
async function loadSummary() {
  try {
    summary.value = await api('/api/instances/summary')
  } catch {
    /* transient; keep the last snapshot */
  }
}

// New server releases (#177), for the list's footer and this server's attention line.
const updates = ref([])
async function loadUpdates() {
  try {
    const auto = await api('/api/serverfiles/auto-update')
    updates.value = auto.branches.filter((b) => b.update_available)
  } catch {
    /* keep last */
  }
}

// On a narrow screen the list gives way to a picker above the page.
function pick(event) {
  router.push({ name: 'instance-detail', params: tabParams(event.target.value, currentTab.value) })
}

let summaryPoll = null
let updatesPoll = null
onMounted(() => {
  loadSummary()
  loadUpdates()
  summaryPoll = setInterval(loadSummary, 5000)
  updatesPoll = setInterval(loadUpdates, 60000)
})
onUnmounted(() => {
  clearInterval(summaryPoll)
  clearInterval(updatesPoll)
})
</script>

<template>
  <div class="rsm-server-layout">
    <ServerList
      class="d-none d-lg-flex"
      :summary="summary"
      :current-id="id"
      :tab="currentTab"
      :updates="updates.length"
    />

    <div class="rsm-server-main">
      <div class="d-lg-none mb-3 d-flex align-items-center gap-2">
        <router-link :to="{ name: 'instances' }" class="small text-nowrap">← All servers</router-link>
        <template v-if="summary && summary.servers.length > 1">
          <label class="visually-hidden" for="server-picker">Server</label>
          <select id="server-picker" class="form-select form-select-sm" :value="String(id)" @change="pick">
            <option v-for="s in summary.servers" :key="s.id" :value="String(s.id)">{{ s.name }}</option>
          </select>
        </template>
      </div>

      <ServerPane
        :id="id"
        :key="id"
        :tab="currentTab"
        :summary="summary"
        :updates="updates"
        @changed="loadSummary"
      />
    </div>
  </div>
</template>

<style scoped>
.rsm-server-layout {
  display: grid;
  grid-template-columns: 17rem minmax(0, 1fr);
  gap: 1.5rem;
  align-items: start;
  max-width: 1600px;
  padding: 0 1.5rem;
}

@media (max-width: 991.98px) {
  .rsm-server-layout {
    grid-template-columns: minmax(0, 1fr);
    padding: 0 0.75rem;
  }
}
</style>
