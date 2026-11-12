<script setup>
// Who is on (#126), on its own tab (#189), as a full-width table (#213). Everything
// here is folded from the server log's join/leave lines; the count from the stats
// line stays the source of truth for how many are online. Every column but the
// name is best-effort — a build that doesn't print a detail leaves it as "—".
import { computed, ref, watch } from 'vue'
import { api } from '../../api'
import { formatUptime } from '../../format.js'
import { BAN_DURATIONS, banBody } from '../../players.js'

const props = defineProps({
  inst: { type: Object, required: true },
  stats: { type: Object, default: null },
  maxPlayers: { type: Number, default: null },
})

// Kick and ban go through the server's own RCON, which is on only when its
// template sets an RCON password, and may act only with the 'admin' permission.
const rcon = ref({ enabled: false, permission: null })
async function loadRcon() {
  try {
    rcon.value = await api(`/api/instances/${props.inst.id}/rcon`)
  } catch {
    rcon.value = { enabled: false, permission: null }
  }
}
// Read again at each start: the server's config.json is rewritten then.
watch(() => props.inst.status, loadRcon, { immediate: true })
const canAct = computed(() => rcon.value.enabled && rcon.value.permission === 'admin')

const busy = ref('')
const message = ref({ kind: '', text: '' })
function say(kind, text) {
  message.value = { kind, text }
}

async function kick(p) {
  if (!window.confirm(`Kick ${p.name}? They can join again.`)) return
  busy.value = `kick-${p.player_num}`
  try {
    const res = await api(`/api/instances/${props.inst.id}/players/kick`, {
      method: 'POST', body: { player_id: p.player_id },
    })
    say('success', `Kicked ${p.name}.${res.reply ? ` The server said: ${res.reply}` : ''}`)
  } catch (e) {
    say('danger', `Could not kick ${p.name}: ${e.message}`)
  } finally {
    busy.value = ''
  }
}

// The ban dialog
const banning = ref(null)
const banDuration = ref(86400)
const banReason = ref('')
function openBan(p) {
  banning.value = p
  banDuration.value = 86400
  banReason.value = ''
}
async function confirmBan() {
  const p = banning.value
  const body = banBody(p, banDuration.value, banReason.value)
  if (!body) return
  busy.value = `ban-${p.player_num}`
  try {
    const res = await api(`/api/instances/${props.inst.id}/players/ban`, { method: 'POST', body })
    const how = BAN_DURATIONS.find((d) => d.seconds === banDuration.value)?.label.toLowerCase()
    say('success', `Banned ${p.name} (${how}).${res.reply ? ` The server said: ${res.reply}` : ''}`)
    banning.value = null
  } catch (e) {
    say('danger', `Could not ban ${p.name}: ${e.message}`)
  } finally {
    busy.value = ''
  }
}

// The server's own ban list, as it prints it
const bans = ref(null)
const banPage = ref(1)
const unbanId = ref('')
async function loadBans(page = banPage.value) {
  busy.value = 'bans'
  try {
    bans.value = (await api(`/api/instances/${props.inst.id}/bans?page=${page}`)).reply
    banPage.value = page
  } catch (e) {
    say('danger', `Could not read the ban list: ${e.message}`)
  } finally {
    busy.value = ''
  }
}
async function unban() {
  const id = unbanId.value.trim()
  if (!id || !window.confirm(`Lift ban ${id}?`)) return
  busy.value = 'unban'
  try {
    await api(`/api/instances/${props.inst.id}/bans/${encodeURIComponent(id)}`, { method: 'DELETE' })
    say('success', `Lifted ban ${id}.`)
    unbanId.value = ''
    await loadBans()
  } catch (e) {
    say('danger', `Could not lift ban ${id}: ${e.message}`)
  } finally {
    busy.value = ''
  }
}

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
            <th v-if="canAct" scope="col"><span class="visually-hidden">Actions</span></th>
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
            <td v-if="canAct" class="text-nowrap text-end">
              <button
                type="button"
                class="btn btn-sm btn-outline-secondary me-1"
                :disabled="!!busy || p.player_id == null"
                :title="p.player_id == null ? 'The log has not given this player\'s id yet' : 'Disconnect them; they can join again'"
                @click="kick(p)"
              >Kick</button>
              <button
                type="button"
                class="btn btn-sm btn-outline-danger"
                :disabled="!!busy || !banBody(p, 0)"
                title="Disconnect them and keep them out"
                @click="openBan(p)"
              >Ban…</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="card-body">
      <p v-if="stats.players && stats.battleye === false" class="text-secondary fst-italic mb-0">
        {{ stats.players }} online. Their names are read from BattlEye's lines in the log,
        and BattlEye is off in this server's template, so there are none to list.
      </p>
      <p v-else-if="stats.players" class="text-secondary fst-italic mb-0">
        {{ stats.players }} online — this server build doesn’t print player names to the log.
      </p>
      <p v-else class="text-secondary fst-italic mb-0">No one connected.</p>
    </div>

    <div v-if="message.text" :class="`alert alert-${message.kind} rounded-0 mb-0 py-2 small`" role="status">
      {{ message.text }}
    </div>

    <!-- The server's own ban list, read over RCON -->
    <div v-if="inst.status === 'running' && rcon.enabled" class="card-footer small">
      <div class="d-flex flex-wrap align-items-center gap-2">
        <span class="fw-semibold me-auto">Ban list on the server</span>
        <button type="button" class="btn btn-sm btn-outline-secondary" :disabled="!!busy" @click="loadBans(1)">
          {{ bans === null ? 'Show' : 'Refresh' }}
        </button>
      </div>
      <template v-if="bans !== null">
        <pre class="rsm-bans mt-2 mb-2">{{ bans || 'The server printed nothing.' }}</pre>
        <div class="d-flex flex-wrap align-items-center gap-2">
          <button type="button" class="btn btn-sm btn-link p-0" :disabled="!!busy || banPage <= 1" @click="loadBans(banPage - 1)">← Previous</button>
          <span class="text-secondary">Page {{ banPage }}</span>
          <button type="button" class="btn btn-sm btn-link p-0" :disabled="!!busy" @click="loadBans(banPage + 1)">Next →</button>
          <span v-if="canAct" class="d-inline-flex gap-1 ms-auto">
            <input
              v-model="unbanId"
              class="form-control form-control-sm"
              style="max-width: 10rem"
              placeholder="Ban ID"
              aria-label="Ban ID to lift"
              @keydown.enter.prevent="unban"
            />
            <button type="button" class="btn btn-sm btn-outline-secondary" :disabled="!!busy || !unbanId.trim()" @click="unban">Lift ban</button>
          </span>
        </div>
      </template>
      <p class="text-secondary mt-2 mb-0">
        Bans made here are sent by the server to Bohemia's online services, which keep
        them for this server. They are separate from the ban list in the template,
        which every server on that template gets.
      </p>
    </div>
    <div v-else-if="inst.status === 'running'" class="card-footer small text-secondary">
      To kick and ban from here, set an RCON password in this server's template
      (Settings step), then restart the server.
    </div>

    <!-- Ban a player -->
    <div v-if="banning" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <form class="modal-content" @submit.prevent="confirmBan">
          <div class="modal-header">
            <h5 class="modal-title">Ban {{ banning.name }}?</h5>
            <button type="button" class="btn-close" aria-label="Close" @click="banning = null"></button>
          </div>
          <div class="modal-body">
            <label for="ban-duration" class="form-label">For</label>
            <select id="ban-duration" v-model.number="banDuration" class="form-select mb-3">
              <option v-for="d in BAN_DURATIONS" :key="d.seconds" :value="d.seconds">{{ d.label }}</option>
            </select>
            <label for="ban-reason" class="form-label">Reason <span class="text-secondary">(optional)</span></label>
            <input id="ban-reason" v-model="banReason" class="form-control" maxlength="120" />
            <p class="form-text mb-0">
              They are disconnected at once and cannot join this server until the ban ends.
            </p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-outline-secondary" @click="banning = null">Cancel</button>
            <button type="submit" class="btn btn-danger" :disabled="!!busy">Ban</button>
          </div>
        </form>
      </div>
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

.rsm-bans {
  max-height: 14rem;
  overflow: auto;
  white-space: pre-wrap;
}

.rsm-roster .rsm-id-label {
  display: inline-block;
  min-width: 4.5rem;
}
</style>
