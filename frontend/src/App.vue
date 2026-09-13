<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import RailIcon from './components/RailIcon.vue'
import { HELP, isUnder, NAV } from './nav'
import { setAuthed } from './router'

const route = useRoute()
const router = useRouter()
const version = ref(null)

async function logout() {
  await api('/api/auth/logout', { method: 'POST' })
  setAuthed(false)
  router.push({ name: 'login' })
}

// --- The rail (#189) ---------------------------------------------------------
// Six top tabs became three destinations and Help, down the left edge on a wide
// screen and along the bottom on a narrow one. The old navbar's live server chips
// (#117) are gone with it: server status belongs to the Servers page.

// Server-file releases the daily Steam check found (#177), shown as a count on
// System so an update is noticed from any page, not only from the Servers page.
const updates = ref(0)
async function loadUpdates() {
  if (route.meta.public) return // no session on the login page
  try {
    const auto = await api('/api/serverfiles/auto-update')
    updates.value = auto.branches.filter((b) => b.update_available).length
  } catch {
    /* transient; keep the last count */
  }
}

let updatePoll = null
onMounted(async () => {
  try {
    version.value = await api('/api/version')
  } catch {
    /* ignore */
  }
  loadUpdates()
  // The check itself runs once a day on the server; a minute is plenty to notice it.
  updatePoll = setInterval(loadUpdates, 60000)
})
onUnmounted(() => clearInterval(updatePoll))
</script>

<template>
  <router-view v-if="route.meta.public" />

  <div v-else class="rsm-shell">
    <nav class="rsm-rail" aria-label="Main">
      <router-link to="/servers" class="rsm-brand" title="Reforger Server Manager">RSM</router-link>

      <router-link
        v-for="item in NAV"
        :key="item.key"
        :to="item.to"
        class="rsm-rail-item"
        :class="{ active: isUnder(route.path, item.to) }"
        :aria-current="isUnder(route.path, item.to) ? 'page' : undefined"
        :title="item.full"
      >
        <RailIcon :name="item.key" />
        <span>{{ item.label }}</span>
        <span
          v-if="item.key === 'system' && updates"
          class="rsm-badge"
          :title="`${updates} new server release${updates > 1 ? 's' : ''}`"
        >{{ updates }}</span>
      </router-link>

      <div class="rsm-rail-foot">
        <router-link
          :to="HELP.to"
          class="rsm-rail-item"
          :class="{ active: isUnder(route.path, HELP.to) }"
          :aria-current="isUnder(route.path, HELP.to) ? 'page' : undefined"
          :title="HELP.full"
        >
          <RailIcon name="help" />
          <span>{{ HELP.label }}</span>
        </router-link>
        <button
          v-if="!version || version.auth_enabled"
          type="button"
          class="rsm-rail-item"
          title="Log out"
          @click="logout"
        >
          <RailIcon name="logout" />
          <span>Log out</span>
        </button>
        <a
          v-if="version && version.version"
          :href="version.repo_url"
          target="_blank"
          rel="noopener"
          class="rsm-version"
          :title="'Open ' + version.name + ' on GitHub'"
        >v{{ version.version }}</a>
      </div>
    </nav>

    <main class="rsm-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.rsm-shell {
  display: flex;
  min-height: 100vh;
}

.rsm-rail {
  position: sticky;
  top: 0;
  height: 100vh;
  width: 5rem;
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
  padding: 0.9rem 0 0.75rem;
  background: var(--bs-tertiary-bg);
  border-right: 1px solid var(--bs-border-color);
}

.rsm-brand {
  font-family: var(--bs-font-monospace);
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--bs-secondary-color);
  text-decoration: none;
  margin-bottom: 0.9rem;
}

.rsm-rail-item {
  position: relative;
  width: 4.1rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.2rem;
  padding: 0.55rem 0 0.45rem;
  border: 0;
  border-radius: 0.5rem;
  background: none;
  font-size: 0.72rem;
  line-height: 1.1;
  color: var(--bs-secondary-color);
  text-decoration: none;
}

.rsm-rail-item:hover,
.rsm-rail-item:focus-visible {
  color: var(--bs-emphasis-color);
  background: var(--bs-secondary-bg);
}

.rsm-rail-item.active {
  color: var(--bs-emphasis-color);
  background: var(--bs-secondary-bg);
}

/* The page you are on also gets a primary-coloured icon, so the rail reads at a
   glance without relying on the fill alone. */
.rsm-rail-item.active svg {
  color: var(--bs-primary);
}

.rsm-badge {
  position: absolute;
  top: 0.2rem;
  right: 0.7rem;
  min-width: 1rem;
  height: 1rem;
  padding: 0 0.25rem;
  border-radius: 0.5rem;
  background: var(--bs-primary);
  color: #fff;
  font-size: 0.65rem;
  font-weight: 600;
  line-height: 1rem;
  text-align: center;
}

.rsm-rail-foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
}

.rsm-version {
  margin-top: 0.35rem;
  font-size: 0.68rem;
  color: var(--bs-secondary-color);
  text-decoration: none;
}

.rsm-version:hover {
  color: var(--bs-emphasis-color);
}

.rsm-main {
  flex: 1;
  min-width: 0;
  padding: 1.5rem 0 3rem;
}

/* Narrow screens: the rail becomes a bottom bar of the same items. Brand and
   version drop out; the System pages show the version too. */
@media (max-width: 991.98px) {
  .rsm-shell {
    display: block;
  }

  .rsm-rail {
    position: fixed;
    inset: auto 0 0 0;
    z-index: 1030;
    height: auto;
    width: auto;
    flex-direction: row;
    justify-content: space-around;
    padding: 0.25rem 0.5rem calc(0.25rem + env(safe-area-inset-bottom));
    border-right: 0;
    border-top: 1px solid var(--bs-border-color);
  }

  .rsm-brand,
  .rsm-version {
    display: none;
  }

  .rsm-rail-foot {
    display: contents;
  }

  .rsm-rail-item {
    width: auto;
    min-width: 3.6rem;
  }

  .rsm-main {
    padding-bottom: 5.5rem;
  }
}
</style>
