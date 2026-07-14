<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const net = ref(null)
const os = ref('linux')
const copied = ref(false)

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
  } catch {
    /* the panel simply stays hidden */
  }
})
</script>

<template>
  <details v-if="net" class="card mb-3">
    <summary class="card-body py-2 px-3 d-flex align-items-center gap-2" style="cursor: pointer">
      <span class="fw-semibold">Ports &amp; firewall</span>
      <span class="small text-secondary">
        game {{ net.game_port_range }}/udp · A2S {{ net.a2s_port_range }}/udp
      </span>
      <span class="badge text-bg-secondary ms-auto">{{ os === 'windows' ? 'Windows' : 'Linux' }}</span>
    </summary>

    <div class="card-body border-top pt-3">
      <p class="small text-secondary">
        Each instance leases one UDP port of each kind from these ranges. Players need the
        <strong>game</strong> port (to join) and the <strong>A2S</strong> port (to see the server in
        the browser) reachable — open them in this machine's firewall and forward them on your
        router. Leave RCON ({{ net.rcon_port_range }}) and the web GUI closed to the internet.
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
  </details>
</template>
