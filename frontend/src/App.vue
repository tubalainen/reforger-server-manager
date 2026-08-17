<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { setAuthed } from './router'
import { serverStatus } from './status'

const route = useRoute()
const router = useRouter()
const version = ref(null)

async function logout() {
  await api('/api/auth/logout', { method: 'POST' })
  setAuthed(false)
  router.push({ name: 'login' })
}

// --- The tabs (#175) ---------------------------------------------------------
// Six destinations, a live status area and the version link share one row, and
// the full page names ("Server Templates", "Mods Overview", …) stopped fitting
// once Backup arrived: they broke mid-word and the bar grew to three lines.
// Short labels here, the full name in the tooltip, and nothing wraps.
const NAV = [
  { to: '/', label: 'Templates', full: 'Server templates' },
  { to: '/mod-templates', label: 'Mod Templates', full: 'Mod templates' },
  { to: '/mods', label: 'Mods', full: 'Mods overview' },
  { to: '/instances', label: 'Instances', full: 'Server instances' },
  { to: '/backup', label: 'Backup', full: 'Backup & restore' },
  { to: '/guide', label: 'Guide', full: 'User guide' },
]

// Which tab owns the page you are on. Matched by prefix rather than by
// router-link's own active class so a sub-page highlights its tab too — the
// wizard belongs to Templates, an instance's page to Instances.
function isActive(item) {
  const path = route.path
  if (item.to === '/') return path === '/' || path.startsWith('/templates')
  return path === item.to || path.startsWith(`${item.to}/`)
}

// The collapsed (hamburger) menu on narrow screens. Bootstrap's own collapse
// needs its JavaScript bundle, which this app deliberately doesn't ship — the
// `show` class is all the CSS needs, so Vue toggles it.
const navOpen = ref(false)
watch(() => route.fullPath, () => (navOpen.value = false))

// --- Top-banner server status bar (#117) ---
// A compact live view of every instance so you see at a glance which servers are
// up and how busy they are from anywhere in the app. It is also the first thing
// to give up room (#175): a chip per server on a wide screen, a one-line summary
// when the tabs need the space, and nothing at all below the hamburger
// breakpoint — the Instances page carries its own fuller bar there.
const summary = ref(null)
const servers = computed(() => summary.value?.servers || [])
const CHIP_LIMIT = 4

// Whether chips are worth showing at all is a question of how many servers
// there are; whether they *fit* is a question of screen width, and that half is
// left to Bootstrap's own breakpoints rather than a resize listener — so the two
// blocks below carry the display classes that decide which one you see.
const chipsFit = computed(() => servers.value.length <= CHIP_LIMIT)
const chipClass = 'd-none d-xl-flex' // only ever rendered when chipsFit
const summaryClass = computed(() =>
  chipsFit.value ? 'd-none d-lg-flex d-xl-none' : 'd-none d-lg-flex',
)

async function loadSummary() {
  if (route.meta.public) return // no session on the login page
  try {
    summary.value = await api('/api/instances/summary')
  } catch {
    /* transient; keep the last snapshot */
  }
}

// A filled dot in the server's status colour (green online, amber loading, etc.).
function dotClass(s) {
  return serverStatus(s.status, s.server_state).cls.replace('text-bg-', 'bg-')
}
function chipTitle(s) {
  const label = serverStatus(s.status, s.server_state).long
  const players = s.status === 'running' && s.server_state !== 'starting'
    ? ` · ${s.players ?? '—'} players` : ''
  return `${s.name}: ${label}${players}`
}

let summaryPoll = null
onMounted(async () => {
  try {
    version.value = await api('/api/version')
  } catch {
    /* ignore */
  }
  loadSummary()
  summaryPoll = setInterval(loadSummary, 8000)
})
onUnmounted(() => clearInterval(summaryPoll))
</script>

<template>
  <nav
    v-if="!route.meta.public"
    class="navbar navbar-expand-lg bg-body-tertiary border-bottom mb-4"
  >
    <div class="container">
      <router-link class="navbar-brand fw-semibold text-nowrap" to="/">
        ⬢ Reforger Server Manager
      </router-link>

      <button
        class="navbar-toggler border-0 px-2"
        type="button"
        :aria-expanded="navOpen"
        aria-label="Toggle navigation"
        @click="navOpen = !navOpen"
      >
        <span class="navbar-toggler-icon"></span>
      </button>

      <div class="collapse navbar-collapse" :class="{ show: navOpen }">
        <!-- nav-pills so the page you are on is a filled pill, in Bootstrap's own
             active-tab styling rather than a bespoke one (#175) -->
        <ul class="navbar-nav nav-pills me-auto mb-2 mb-lg-0">
          <li v-for="item in NAV" :key="item.to" class="nav-item">
            <router-link
              class="nav-link"
              :class="{ active: isActive(item) }"
              :title="item.full"
              :to="item.to"
            >{{ item.label }}</router-link>
          </li>
        </ul>

        <!-- Live server status (#117): a chip per server on a wide screen… -->
        <div
          v-if="summary && summary.total && chipsFit"
          class="align-items-center gap-3 me-3 small"
          :class="chipClass"
        >
          <router-link
            v-for="s in servers"
            :key="s.id"
            :to="{ name: 'instance-detail', params: { id: s.id } }"
            class="rsm-chip d-inline-flex align-items-center gap-1 text-decoration-none text-body"
            :title="chipTitle(s)"
          >
            <span class="rsm-dot rounded-circle" :class="dotClass(s)"></span>
            <span class="text-truncate">{{ s.name }}</span>
            <span
              v-if="s.status === 'running' && s.server_state !== 'starting'"
              class="text-secondary"
            >{{ s.players ?? '—' }}👤</span>
          </router-link>
        </div>
        <!-- …and one summary line when the tabs need that space back (#175) -->
        <router-link
          v-if="summary && summary.total"
          :to="{ name: 'instances' }"
          class="align-items-center gap-2 me-3 small text-decoration-none text-body text-nowrap"
          :class="summaryClass"
          :title="`${summary.running} of ${summary.total} servers online`"
        >
          <span class="fw-semibold">{{ summary.running }}</span>
          <span class="text-secondary">/ {{ summary.total }} online</span>
          <span class="text-secondary">· {{ summary.players_total }}👤</span>
        </router-link>

        <div class="d-flex align-items-center gap-3">
          <a
            v-if="version && version.version"
            :href="version.repo_url"
            target="_blank"
            rel="noopener"
            class="navbar-text small text-secondary text-nowrap text-decoration-none p-0"
            :title="'Open ' + version.name + ' on GitHub'"
          >v{{ version.version }} ↗</a>
          <button
            v-if="!version || version.auth_enabled"
            class="btn btn-outline-secondary btn-sm"
            @click="logout"
          >Log out</button>
        </div>
      </div>
    </div>
  </nav>
  <router-view />
</template>

<style scoped>
/* One row, shared by six tabs, the live status and the version link. Every part
   of it is kept on a single line on purpose: labels breaking mid-word is what
   made the bar look broken (#175). */
.navbar .navbar-nav .nav-link {
  white-space: nowrap;
  padding: 0.3rem 0.7rem;
}

/* Only the unselected tabs are dimmed — the selected one keeps the pill's own
   white-on-primary, which this would otherwise outrank. */
.navbar .navbar-nav .nav-link:not(.active) {
  color: var(--bs-secondary-color);
}

/* The active pill itself comes from .nav-pills; this is the matching hover, so
   an unselected tab reacts to the pointer instead of sitting inert. */
.navbar .navbar-nav .nav-link:hover,
.navbar .navbar-nav .nav-link:focus-visible {
  background-color: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

/* Chips are the widest optional thing up here, so their names are capped
   tighter than the tabs need — the full name is in the tooltip. */
.rsm-chip {
  max-width: 8rem;
}

.rsm-dot {
  width: 0.6rem;
  height: 0.6rem;
  flex: 0 0 auto;
}

/* The tightest band: still one row (the hamburger starts below 992px), but the
   brand, six tabs, the status summary and the version link together need more
   than the 960px container gives them — so the brand and the tab padding give a
   little back rather than the row overflowing. */
@media (min-width: 992px) and (max-width: 1199.98px) {
  .navbar .navbar-brand {
    font-size: 1.05rem;
  }

  .navbar .navbar-nav .nav-link {
    padding-inline: 0.5rem;
  }
}

/* Stacked in the hamburger menu, the tabs read as a list rather than a row. */
@media (max-width: 991.98px) {
  .navbar .navbar-nav .nav-link {
    padding: 0.4rem 0.75rem;
  }
}
</style>
