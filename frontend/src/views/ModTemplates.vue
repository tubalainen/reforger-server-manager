<script setup>
// Mod templates (issue #166): named, reusable mod lists you keep on a shelf and
// load into the Mods step of any server template. This page is the shelf: create,
// edit, copy, release, delete, and read the change log of each one. Like server
// templates they move Test → Dev → Prod, and Prod is locked (#223).
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import ChangeLogModal from '../components/ChangeLogModal.vue'
import HelpTip from '../components/HelpTip.vue'
import MenuButton from '../components/MenuButton.vue'
import NewFromProdDialog from '../components/NewFromProdDialog.vue'
import ReleaseDialog from '../components/ReleaseDialog.vue'
import StageBadge from '../components/StageBadge.vue'
import { groupByStage, isLocked, promoteAction, versionsText } from '../stages'

const router = useRouter()
const modTemplates = ref([])
const loading = ref(true)
const error = ref('')

// The change-log modal is the very one the Server Templates page uses (#112).
const logTarget = ref(null)

const groups = computed(() => groupByStage(modTemplates.value))
const prods = computed(() => modTemplates.value.filter((m) => m.stage === 'prod'))

async function load() {
  try {
    modTemplates.value = await api('/api/mod-templates')
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function remove(mt) {
  if (!confirm(
    `Delete the mod list "${mt.name}"?\n\nServer templates you built from it keep their mods — this only removes the list itself, and its change log.`,
  )) return
  if (isLocked(mt) && !confirm(
    `"${mt.name}" is a Prod mod list. Deleting it also deletes ${versionsText(mt.prod_version)}, `
    + 'for good.\n\nReally delete it?',
  )) return
  try {
    await api(`/api/mod-templates/${mt.id}`, { method: 'DELETE' })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

const copying = ref(0)
async function copy(mt) {
  copying.value = mt.id
  error.value = ''
  try {
    const created = await api(`/api/mod-templates/${mt.id}/copy`, { method: 'POST' })
    router.push({ name: 'mod-template-edit', params: { id: created.id } })
  } catch (e) {
    error.value = e.message
  } finally {
    copying.value = 0
  }
}

const promoting = ref(null)
async function promoted() {
  promoting.value = null
  await load()
}

const newFromProd = ref(false)
function createdFromProd(created) {
  newFromProd.value = false
  router.push({ name: 'mod-template-edit', params: { id: created.id } })
}

onMounted(load)
</script>

<template>
  <div class="container">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">Mod lists</h1>
      <MenuButton label="+ New mod list" button-class="btn btn-primary">
        <li>
          <button class="dropdown-item" @click="router.push({ name: 'mod-template-new' })">
            Blank mod list
          </button>
        </li>
        <li>
          <button class="dropdown-item" @click="newFromProd = true">
            Based on a Prod mod list…
          </button>
        </li>
      </MenuButton>
    </div>

    <p class="text-secondary small">
      Sets of mods you reuse, ready to load into any server template.
      <HelpTip label="About mod lists">
        <p>
          A mod list is a saved set of mods — nothing else, no scenario and no server
          settings. Build the set once here, then load it into the <strong>Mods</strong>
          step of any server template, in the order you arranged it.
        </p>
        <p>
          To start from a server template you already like, use <em>To mod list</em>
          in its ⋯ menu under Server templates — its scenario mods are left out.
        </p>
        <p>
          Like server templates, a mod list starts in Test, moves to Dev, and is
          released to Prod, where it is locked. Copy a Prod list to change it, and
          release the copy as its next version.
        </p>
        <p>Every change is recorded in the list's own change log.</p>
      </HelpTip>
    </p>

    <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>

    <div v-if="loading" class="text-secondary">Loading…</div>

    <div v-else-if="!modTemplates.length" class="card text-center text-secondary py-5">
      <div class="card-body">
        <p class="fs-1 mb-2">🧰</p>
        <p class="mb-1">No mod lists yet.</p>
        <p class="small mb-0">
          Create one to keep a set of mods you use over and over — a milsim pack, a
          training set — and load it into any server template in two clicks.
        </p>
      </div>
    </div>

    <template v-else>
      <section v-for="g in groups" :key="g.stage" class="mb-4">
        <h2 class="h6 d-flex align-items-center gap-2 mb-2">
          <StageBadge :stage="g.stage" />
          <span class="text-secondary fw-normal small">{{ g.hint }}</span>
        </h2>
        <div class="list-group">
          <div
            v-for="mt in g.items"
            :key="mt.id"
            class="list-group-item d-flex justify-content-between align-items-center gap-2 flex-wrap"
          >
            <!-- Takes the free space and wraps its text inside it, so a long
                 description no longer pushes the buttons below the row. -->
            <div class="rsm-row-text">
              <div class="fw-semibold">
                {{ mt.name }}
                <StageBadge v-if="isLocked(mt)" stage="prod" :version="mt.prod_version" class="ms-1" />
                <span class="badge text-bg-secondary ms-1">{{ mt.mod_count }} mod{{ mt.mod_count === 1 ? '' : 's' }}</span>
                <span
                  v-if="mt.locked_count"
                  class="badge border text-secondary-emphasis ms-1"
                  :title="`${mt.locked_count} mod(s) locked to a specific version`"
                >🔒 {{ mt.locked_count }} locked</span>
              </div>
              <div class="small text-secondary">
                {{ mt.description || 'No description' }}
                <span v-if="mt.based_on">
                  · based on {{ mt.based_on.name || 'a deleted mod list' }}<template v-if="mt.based_on.version"> v{{ mt.based_on.version }}</template>
                </span>
              </div>
            </div>
            <div class="d-flex gap-1 flex-shrink-0">
              <button
                class="btn btn-sm btn-outline-primary"
                :title="isLocked(mt) ? 'Prod mod lists are locked: open it read-only' : ''"
                @click="router.push({ name: 'mod-template-edit', params: { id: mt.id } })"
              >{{ isLocked(mt) ? 'View' : 'Edit' }}</button>
              <button
                v-if="promoteAction(mt.stage)"
                class="btn btn-sm"
                :class="promoteAction(mt.stage).release ? 'btn-outline-success' : 'btn-outline-info'"
                @click="promoting = mt"
              >{{ promoteAction(mt.stage).label }}</button>
              <MenuButton label="⋯" aria-label="More actions">
                <li><button class="dropdown-item" @click="logTarget = mt">Change log</button></li>
                <li>
                  <button class="dropdown-item" :disabled="copying === mt.id" @click="copy(mt)">
                    {{ copying === mt.id ? 'Copying…' : 'Copy to Test' }}
                  </button>
                </li>
                <li><hr class="dropdown-divider" /></li>
                <li><button class="dropdown-item text-danger" @click="remove(mt)">Delete</button></li>
              </MenuButton>
            </div>
          </div>
        </div>
      </section>
    </template>

    <ChangeLogModal
      v-if="logTarget"
      :url="`/api/mod-templates/${logTarget.id}/changelog`"
      :subtitle="logTarget.name"
      @close="logTarget = null"
    />
    <ReleaseDialog
      v-if="promoting"
      :item="promoting"
      :items="modTemplates"
      base="/api/mod-templates"
      noun="mod list"
      @close="promoting = null"
      @done="promoted"
    />
    <NewFromProdDialog
      v-if="newFromProd"
      :prods="prods"
      base="/api/mod-templates"
      noun="mod list"
      @close="newFromProd = false"
      @created="createdFromProd"
    />
  </div>
</template>

<style scoped>
/* The name and description take the free space and wrap inside it; below about
   20rem (a phone) the buttons move under the text instead of squeezing it. */
.rsm-row-text {
  flex: 1 1 20rem;
  min-width: 0;
}
</style>
