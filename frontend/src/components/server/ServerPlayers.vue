<script setup>
// Who is on (#126), on its own tab (#189), as a full-width table (#213). Everything
// here is folded from the server log's join/leave lines; the count from the stats
// line stays the source of truth for how many are online. Every column but the
// name is best-effort — a build that doesn't print a detail leaves it as "—".
import { ref, watch } from 'vue'
import { formatUptime } from '../../format.js'

defineProps({
  inst: { type: Object, required: true },
  stats: { type: Object, default: null },
  maxPlayers: { type: Number, default: null },
})

// Addresses are hidden until asked for, so the tab can be screenshotted for a
// bug report without painting over them. Remembered per browser.
const IP_KEY = 'rsm.players.showIp'
const showIp = ref(false)
try {
  showIp.value = localStorage.getItem(IP_KEY) === '1'
} catch {
  /* storage blocked — default to hidden */
}
watch(showIp, (on) => {
  try {
    localStorage.setItem(IP_KEY, on ? '1' : '0')
  } catch {
    /* storage blocked — the toggle still works for this visit */
  }
})

const copied = ref('')
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = text
    setTimeout(() => {
      if (copied.value === text) copied.value = ''
    }, 2000)
  } catch {
    /* clipboard blocked (no https / no permission) — the id is selectable anyway */
  }
}

function joinedAt(p) {
  return p.connected_at ? `Joined ${new Date(p.connected_at).toLocaleString()}` : ''
}
</script>

<template>
  <div class="card">
    <div class="card-header py-2 d-flex align-items-center gap-3 flex-wrap">
      <span class="fw-semibold small">
        <template v-if="inst.status === 'running' && stats && stats.players != null">
          {{ stats.players }}{{ maxPlayers != null ? ` of ${maxPlayers}` : '' }} online
        </template>
        <template v-else>Players</template>
      </span>
      <div
        v-if="stats && stats.roster && stats.roster.length"
        class="form-check form-switch small mb-0 ms-auto"
      >
        <input id="rsm-show-ip" v-model="showIp" class="form-check-input" type="checkbox" />
        <label class="form-check-label" for="rsm-show-ip">Show IP addresses</label>
      </div>
    </div>
    <div v-if="inst.status !== 'running'" class="card-body">
      <p class="text-secondary mb-0">No one can be connected while the server isn't running.</p>
    </div>
    <div v-else-if="!stats" class="card-body">
      <p class="text-secondary mb-0">Loading…</p>
    </div>
    <div v-else-if="stats.roster && stats.roster.length" class="table-responsive">
      <table class="table table-sm mb-0 small rsm-roster">
        <thead class="text-nowrap">
          <tr>
            <th scope="col" title="The server's player id for this session">ID</th>
            <th scope="col">Name</th>
            <th scope="col">Online for</th>
            <th scope="col">Platform</th>
            <th scope="col">IP address</th>
            <th scope="col">Identifiers</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in stats.roster" :key="p.player_num">
            <td class="text-secondary">{{ p.player_id ?? '—' }}</td>
            <td class="fw-semibold rsm-name">
              {{ p.name }}
              <span
                v-if="p.admin"
                class="badge text-bg-warning ms-1"
                title="Listed under Server admins in this server's template"
              >Admin</span>
            </td>
            <td class="text-nowrap" :title="joinedAt(p)">
              {{ p.online_seconds != null ? formatUptime(p.online_seconds) : '—' }}
            </td>
            <td>{{ p.platform || '—' }}</td>
            <td class="font-monospace text-nowrap">
              <template v-if="!p.ip">—</template>
              <template v-else-if="showIp">{{ p.ip }}</template>
              <span v-else class="text-secondary" title="Turn on “Show IP addresses” above">hidden</span>
            </td>
            <td class="text-nowrap">
              <template v-if="p.identity_id || p.steam_id || p.be_guid">
                <div
                  v-if="p.identity_id"
                  title="What the template's admins, whitelist and ban list match on"
                >
                  <span class="text-secondary rsm-id-label">Identity</span>
                  <span class="font-monospace">{{ p.identity_id }}</span>
                  <button
                    type="button"
                    class="btn btn-link btn-sm p-0 ms-1 align-baseline"
                    title="Copy — paste it into the template's whitelist or ban list"
                    @click="copy(p.identity_id)"
                  >{{ copied === p.identity_id ? 'Copied' : 'Copy' }}</button>
                </div>
                <div v-if="p.steam_id">
                  <span class="text-secondary rsm-id-label">Steam</span>
                  <a
                    class="font-monospace"
                    :href="`https://steamcommunity.com/profiles/${p.steam_id}`"
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Open the Steam profile"
                  >{{ p.steam_id }}</a>
                </div>
                <div v-if="p.be_guid">
                  <span class="text-secondary rsm-id-label">BattlEye</span>
                  <span class="font-monospace text-secondary">{{ p.be_guid }}</span>
                </div>
              </template>
              <template v-else>—</template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="card-body">
      <p v-if="stats.players" class="text-secondary fst-italic mb-0">
        {{ stats.players }} online — this server build doesn’t print player names to the log.
      </p>
      <p v-else class="text-secondary fst-italic mb-0">No one connected.</p>
    </div>
  </div>
</template>

<style scoped>
.rsm-roster th,
.rsm-roster td {
  padding: 0.5rem;
}

.rsm-roster td {
  vertical-align: top;
}

.rsm-roster .rsm-name {
  /* Names are free text and can be long: wrap at spaces rather than clip (#213),
     and break inside a word only when one word alone is wider than the table. */
  overflow-wrap: break-word;
  min-width: 8rem;
}

.rsm-roster .rsm-id-label {
  display: inline-block;
  min-width: 4.5rem;
}
</style>
