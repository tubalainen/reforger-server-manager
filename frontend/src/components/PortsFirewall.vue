<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import HelpTip from './HelpTip.vue'

const net = ref(null)
const os = ref('linux')
const copied = ref(false)
const error = ref('')

const command = computed(() => (net.value ? net.value.firewall[os.value] : ''))

async function copy() {
  try {
    await navigator.clipboard.writeText(command.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    /* clipboard blocked (no https / no permission) — the text is selectable anyway */
  }
}

onMounted(async () => {
  try {
    net.value = await api('/api/system/network')
    os.value = net.value.host
  } catch (e) {
    // This used to be an optional panel that could just hide; on its own page (#189)
    // a blank screen would say nothing, so say what went wrong.
    error.value = e.message
  }
})
</script>

<template>
  <div v-if="error" class="alert alert-warning py-2">
    Could not read this host's port ranges: {{ error }}
  </div>
  <div v-else-if="net" class="card mb-3">
    <div class="card-header py-2 px-3 d-flex align-items-center gap-2">
      <span class="small text-secondary">
        game {{ net.game_port_range }}/udp · A2S {{ net.a2s_port_range }}/udp
      </span>
      <span class="badge text-bg-secondary ms-auto">{{ os === 'windows' ? 'Windows' : 'Linux' }}</span>
    </div>

    <div class="card-body">
      <p class="small text-secondary">
        Open the game and A2S ranges to players; keep RCON and the web GUI private.
        <HelpTip label="Which ports players need">
          <p>
            Each server leases one UDP port of each kind from these ranges. Players need the
            <strong>game</strong> port (to join) and the <strong>A2S</strong> port (to see the
            server in the browser) reachable — open them in this machine's firewall and forward
            them on your router.
          </p>
          <p>Leave RCON ({{ net.rcon_port_range }}) and the web GUI closed to the internet.</p>
        </HelpTip>
      </p>

      <div class="btn-group btn-group-sm mb-2" role="group">
        <button
          class="btn"
          :class="os === 'windows' ? 'btn-primary' : 'btn-outline-secondary'"
          @click="os = 'windows'"
        >Windows (PowerShell)</button>
        <button
          class="btn"
          :class="os === 'linux' ? 'btn-primary' : 'btn-outline-secondary'"
          @click="os = 'linux'"
        >Linux (ufw)</button>
      </div>

      <div class="position-relative">
        <!-- pre-wrap, not Bootstrap's .text-wrap: that is white-space:normal and would
             fold the two ufw lines into one -->
        <pre
          class="bg-body-tertiary border rounded p-3 mb-1 small"
          style="white-space: pre-wrap; word-break: break-word"
        ><code>{{ command }}</code></pre>
        <button class="btn btn-sm btn-outline-secondary position-absolute top-0 end-0 m-2" @click="copy">
          {{ copied ? 'Copied' : 'Copy' }}
        </button>
      </div>
      <p class="small text-secondary mb-0">
        <span v-if="os === 'windows'">Run it once in an <strong>elevated</strong> PowerShell.</span>
        <span v-else>Run it once as a user who may sudo.</span>
        Then forward the same UDP ranges on your router to this machine's LAN IP.
      </p>
    </div>
  </div>
  <p v-else class="text-secondary">Loading…</p>
</template>
