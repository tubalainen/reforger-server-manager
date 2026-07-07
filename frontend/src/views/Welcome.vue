<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { markOnboarded } from '../router'

const router = useRouter()
const filesReady = ref(false)
const hasTemplate = ref(false)
const hasInstance = ref(false)
const loading = ref(true)

async function refresh() {
  loading.value = true
  try {
    const [sf, templates, instances] = await Promise.all([
      api('/api/serverfiles').catch(() => ({ branches: [] })),
      api('/api/templates').catch(() => []),
      api('/api/instances').catch(() => []),
    ])
    filesReady.value = (sf.branches || []).some((b) => b.installed)
    hasTemplate.value = templates.length > 0
    hasInstance.value = instances.length > 0
  } finally {
    loading.value = false
  }
}

const steps = computed(() => [
  {
    n: 1,
    title: 'Download the server files',
    text: 'Fetch the Arma Reforger dedicated-server files (Stable or Experimental) with SteamCMD.',
    done: filesReady.value,
    to: '/downloads',
    cta: 'Go to Downloads',
  },
  {
    n: 2,
    title: 'Create a template',
    text: 'Pick a scenario from the Workshop, resolve its mods, tune settings — this becomes your server config.',
    done: hasTemplate.value,
    to: '/templates/new',
    cta: 'New template',
  },
  {
    n: 3,
    title: 'Create and start a server',
    text: 'Turn a template into a running server instance and watch it come up in the live log.',
    done: hasInstance.value,
    to: '/instances',
    cta: 'Go to Instances',
  },
])

const allDone = computed(() => steps.value.every((s) => s.done))

function finish() {
  markOnboarded()
  router.push('/')
}

function go(to) {
  markOnboarded()
  router.push(to)
}

onMounted(refresh)
</script>

<template>
  <div class="container" style="max-width: 720px">
    <div class="text-center mb-4">
      <h1 class="h3">Welcome to Reforger Server Manager 👋</h1>
      <p class="text-secondary">Three steps to your first running server.</p>
    </div>

    <div class="list-group mb-4">
      <div v-for="s in steps" :key="s.n" class="list-group-item d-flex align-items-center gap-3 py-3">
        <span
          class="d-inline-flex align-items-center justify-content-center rounded-circle flex-shrink-0"
          :class="s.done ? 'text-bg-success' : 'text-bg-secondary'"
          style="width: 2rem; height: 2rem"
        >{{ s.done ? '✓' : s.n }}</span>
        <div class="flex-grow-1">
          <div class="fw-semibold">{{ s.title }}</div>
          <div class="small text-secondary">{{ s.text }}</div>
        </div>
        <button class="btn btn-sm" :class="s.done ? 'btn-outline-secondary' : 'btn-primary'" @click="go(s.to)">
          {{ s.cta }}
        </button>
      </div>
    </div>

    <div class="d-flex justify-content-between align-items-center">
      <button class="btn btn-link text-secondary px-0" @click="finish">Skip — don't show again</button>
      <button v-if="allDone" class="btn btn-success" @click="finish">All set — let's go!</button>
      <button class="btn btn-outline-secondary" :disabled="loading" @click="refresh">Refresh progress</button>
    </div>
  </div>
</template>
