<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { api } from '../../api'
import { formatBytes } from '../../format'
import {
  changedFields,
  describeSteps,
  LOCKED_WHILE_RUNNING,
  mergeIncoming,
  normaliseTimes,
  planSave,
  settingsFrom,
  validate,
} from '../../settingsForm'
import StoredData from '../StoredData.vue'

// A server's settings as one form with one Save (#189). Edit anything, see what is
// unsaved, save it all at once. Fields that rebuild the container are locked while
// the server runs and say so, instead of a greyed-out button that explains nothing.
// Clearing data and deleting the server act at once, so they sit below the form.
const props = defineProps({
  inst: { type: Object, required: true },
  connect: { type: String, default: '' },
  addressDetected: { type: Boolean, default: false },
})
const emit = defineEmits(['update', 'changed', 'delete'])

const running = computed(() => props.inst.status === 'running')
const fmtBytes = formatBytes

// --- The form ------------------------------------------------------------------
// `baseline` is what the server holds, `draft` what the form shows.
const baseline = ref(settingsFrom(props.inst))
const draft = ref(settingsFrom(props.inst))

// The server page re-reads the server every few seconds; fold each copy in without
// throwing away what is being typed.
watch(
  () => props.inst,
  (inst) => {
    const merged = mergeIncoming(baseline.value, draft.value, inst)
    baseline.value = merged.baseline
    draft.value = merged.draft
  },
)

const changed = computed(() => changedFields(baseline.value, draft.value))
const dirty = computed(() => changed.value.length > 0)
const isChanged = (field) => changed.value.includes(field)
const errors = computed(() => validate(draft.value))
const hasErrors = computed(() => Object.keys(errors.value).length > 0)
const isLocked = (field) => running.value && LOCKED_WHILE_RUNNING.includes(field)
// A locked field the user already changed cannot be saved until the server stops.
const lockedChanges = computed(() => changed.value.filter(isLocked))

const plan = computed(() => planSave(baseline.value, draft.value))
const pendingSummary = computed(() => plan.value.map((s) => s.label).join(', '))
// For the template dialog: what else rides along with the template change.
const otherChanges = computed(() =>
  plan.value.filter((s) => s.key !== 'template').map((s) => s.label).join(', '),
)

function discard() {
  draft.value = settingsFrom(props.inst)
  baseline.value = settingsFrom(props.inst)
  error.value = ''
}

// --- Daily restart times live in the draft like everything else ----------------
const newTime = ref('')
function addTime() {
  if (!newTime.value) return
  draft.value.restart_times = normaliseTimes([...draft.value.restart_times, newTime.value])
  newTime.value = ''
}
function removeTime(t) {
  draft.value.restart_times = draft.value.restart_times.filter((x) => x !== t)
}

// --- Templates and the world they read ------------------------------------------
const templates = ref([])
const savedData = ref({ files: 0, size_bytes: 0 })
onMounted(async () => {
  try {
    templates.value = await api('/api/templates')
  } catch {
    /* the picker falls back to the current template only */
  }
  try {
    const data = await api(`/api/instances/${props.inst.id}/data`)
    savedData.value = data.items.find((i) => i.target === 'saves') || savedData.value
  } catch {
    /* no backup offer without knowing there is a world */
  }
})
const templateOptions = computed(() =>
  templates.value.length
    ? templates.value
    : [{ id: props.inst.template_id, name: props.inst.template_name || 'Current template' }],
)
const hasSavedData = computed(() => !!savedData.value.files)
const templateById = (id) => templates.value.find((t) => t.id === id)

// Warn when the new template writes to a different persistent save (#31)
const hiveWarning = computed(() => {
  const cur = templateById(baseline.value.template_id)
  const next = templateById(draft.value.template_id)
  if (!cur || !next || next.id === cur.id) return ''
  if (cur.persistence && next.persistence && cur.hive_id !== next.hive_id)
    return `The hive id changes (${cur.hive_id} → ${next.hive_id}): this server will use a different persistent save.`
  if (cur.persistence && !next.persistence)
    return 'The new template has persistence disabled — existing saved game data will no longer load.'
  if (!cur.persistence && next.persistence)
    return `The new template enables persistence (hive id ${next.hive_id}) — a new save will be created.`
  return ''
})

// --- Save ---------------------------------------------------------------------------
const saving = ref(false)
const error = ref('')
const notice = ref('')
// Ticked by default whenever there is a world to lose: the save the old scenario
// built is exactly what a template swap orphans (#179).
const backupBeforeSwap = ref(true)
const confirmTemplate = ref(false)

function requestSave() {
  if (!dirty.value || hasErrors.value || saving.value) return
  // A template change can point the server at a different world: say so first.
  if (isChanged('template_id')) {
    backupBeforeSwap.value = true
    confirmTemplate.value = true
    return
  }
  save()
}

async function save() {
  confirmTemplate.value = false
  saving.value = true
  error.value = ''
  notice.value = ''
  const done = []
  let backupNote = ''
  for (const step of planSave(baseline.value, draft.value)) {
    const body = step.key === 'template'
      ? { ...step.body, backup_first: backupBeforeSwap.value && hasSavedData.value }
      : step.body
    try {
      const res = await api(`/api/instances/${props.inst.id}${step.path}`, { method: 'PUT', body })
      const { backup, ...view } = res
      if (backup) {
        backupNote = ` The saved game was backed up first (${fmtBytes(backup.size_bytes)}) — it is on the Saves tab.`
      }
      emit('update', view) // the watch above folds it in, which clears those fields
      done.push(step)
    } catch (e) {
      // Stop at the first refusal: later steps may depend on it, and the form still
      // holds everything that did not go through.
      error.value =
        (done.length ? `Saved ${describeSteps(done)}. ` : '') +
        `Could not save the ${step.label}: ${e.message}`
      break
    }
  }
  if (!error.value) notice.value = `Saved.${backupNote}`
  else if (backupNote) notice.value = backupNote.trim()
  if (done.some((s) => s.key === 'template')) emit('changed')
  saving.value = false
}

// --- Don't lose an edit to a stray click ---------------------------------------------
const LEAVE_PROMPT = 'This server has unsaved settings. Leave without saving them?'
function guard() {
  return !dirty.value || window.confirm(LEAVE_PROMPT)
}
onBeforeRouteLeave(guard)
onBeforeRouteUpdate(guard) // another tab, or another server from the list
function beforeUnload(e) {
  if (dirty.value) {
    e.preventDefault()
    e.returnValue = ''
  }
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
</script>

<template>
  <div class="d-grid gap-3">
    <form class="d-grid gap-3" novalidate @submit.prevent="requestSave">
      <div v-if="running" class="alert alert-secondary py-2 small mb-0 d-flex align-items-center gap-2">
        <svg class="rsm-lock" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
        <span id="lock-note">
          The game version, template and ports rebuild the server's container, so they
          can only change while it is stopped. Everything else can change now.
        </span>
      </div>

      <!-- General -->
      <fieldset class="card">
        <legend class="card-header py-2 fw-semibold small mb-0">General</legend>
        <div class="card-body rsm-rows">
          <div class="rsm-row">
            <label for="set-name" class="rsm-row-label">Name</label>
            <div>
              <input
                id="set-name"
                v-model="draft.name"
                class="form-control"
                :class="{ 'is-invalid': errors.name, 'rsm-changed': isChanged('name') }"
                maxlength="100"
                autocomplete="off"
                autocorrect="off"
                spellcheck="false"
              />
              <div v-if="errors.name" class="invalid-feedback">{{ errors.name }}</div>
            </div>
          </div>

          <div class="rsm-row">
            <label for="set-branch" class="rsm-row-label">Game version</label>
            <div>
              <select
                id="set-branch"
                v-model="draft.branch"
                class="form-select"
                :class="{ 'rsm-changed': isChanged('branch') }"
                :disabled="isLocked('branch')"
                :aria-describedby="isLocked('branch') ? 'lock-note' : undefined"
              >
                <option value="stable">Stable</option>
                <option value="experimental">Experimental</option>
              </select>
            </div>
          </div>

          <div class="rsm-row">
            <label for="set-template" class="rsm-row-label">Template</label>
            <div>
              <select
                id="set-template"
                v-model.number="draft.template_id"
                class="form-select"
                :class="{ 'rsm-changed': isChanged('template_id') }"
                :disabled="isLocked('template_id')"
                :aria-describedby="isLocked('template_id') ? 'lock-note' : undefined"
              >
                <option v-for="t in templateOptions" :key="t.id" :value="t.id">{{ t.name }}</option>
              </select>
              <div v-if="hiveWarning" class="form-text text-warning">⚠ {{ hiveWarning }}</div>
              <div v-else class="form-text">
                A different template can read a different saved world; you will be asked before it changes.
              </div>
            </div>
          </div>
        </div>
      </fieldset>

      <!-- Network -->
      <fieldset class="card">
        <legend class="card-header py-2 fw-semibold small mb-0">Network</legend>
        <div class="card-body rsm-rows">
          <div class="rsm-row">
            <span id="ports-label" class="rsm-row-label">Ports (UDP)</span>
            <div>
              <div class="rsm-ports" role="group" aria-labelledby="ports-label">
                <label v-for="p in [['game_port', 'Game'], ['a2s_port', 'A2S'], ['rcon_port', 'RCON']]" :key="p[0]">
                  <span class="small text-secondary">{{ p[1] }}</span>
                  <input
                    v-model.number="draft[p[0]]"
                    type="number"
                    min="1"
                    max="65535"
                    class="form-control rsm-num"
                    :class="{ 'is-invalid': errors[p[0]], 'rsm-changed': isChanged(p[0]) }"
                    :disabled="isLocked(p[0])"
                    :aria-describedby="isLocked(p[0]) ? 'lock-note' : undefined"
                  />
                </label>
              </div>
              <div v-if="errors.game_port || errors.a2s_port || errors.rcon_port" class="small text-danger mt-1">
                {{ errors.game_port || errors.a2s_port || errors.rcon_port }}
              </div>
              <div v-else class="form-text">
                Players need the game port to join and the A2S port to find the server.
              </div>
            </div>
          </div>

          <div class="rsm-row">
            <span class="rsm-row-label">Join address</span>
            <div class="d-flex flex-wrap align-items-center gap-2">
              <template v-if="connect">
                <code>{{ connect }}</code>
                <span
                  v-if="addressDetected"
                  class="badge text-bg-secondary"
                  title="Auto-detected from the server log; set PUBLIC_ADDRESS in .env to override"
                >auto</span>
              </template>
              <span
                v-else
                class="text-secondary fst-italic"
                title="Set PUBLIC_ADDRESS in .env, or start the server so its public IP can be detected from the log"
              >set PUBLIC_ADDRESS in .env</span>
            </div>
          </div>
        </div>
      </fieldset>

      <!-- Automation -->
      <fieldset class="card">
        <legend class="card-header py-2 fw-semibold small mb-0">Automation</legend>
        <div class="card-body rsm-rows">
          <div class="rsm-row">
            <label for="set-auto-restart" class="rsm-row-label">Restart after a crash</label>
            <div class="d-flex align-items-start gap-3">
              <div class="form-check form-switch mb-0 mt-1">
                <input
                  id="set-auto-restart"
                  v-model="draft.auto_restart"
                  class="form-check-input"
                  :class="{ 'rsm-changed': isChanged('auto_restart') }"
                  type="checkbox"
                  role="switch"
                />
              </div>
              <span class="form-text mt-0">Only when its process exits, not after a planned reboot.</span>
            </div>
          </div>

          <div class="rsm-row">
            <label for="set-auto-start" class="rsm-row-label">Start after a host reboot</label>
            <div class="d-flex align-items-start gap-3">
              <div class="form-check form-switch mb-0 mt-1">
                <input
                  id="set-auto-start"
                  v-model="draft.auto_start"
                  class="form-check-input"
                  :class="{ 'rsm-changed': isChanged('auto_start') }"
                  type="checkbox"
                  role="switch"
                />
              </div>
              <span class="form-text mt-0">Also when Docker itself restarts.</span>
            </div>
          </div>

          <div class="rsm-row">
            <label for="set-new-time" class="rsm-row-label">Daily restarts</label>
            <div>
              <div class="d-flex flex-wrap align-items-center gap-2">
                <span
                  v-for="t in draft.restart_times"
                  :key="t"
                  class="badge text-bg-secondary d-inline-flex align-items-center gap-1 rsm-num"
                  :class="{ 'rsm-changed-badge': isChanged('restart_times') }"
                >
                  {{ t }}
                  <button
                    type="button"
                    class="btn-close btn-close-white"
                    style="font-size: 0.5rem"
                    :aria-label="`Remove ${t}`"
                    @click="removeTime(t)"
                  ></button>
                </span>
                <span v-if="!draft.restart_times.length" class="text-secondary small">None</span>
                <span class="d-inline-flex gap-1">
                  <input
                    id="set-new-time"
                    v-model="newTime"
                    type="time"
                    class="form-control form-control-sm"
                    style="max-width: 8rem"
                    @keydown.enter.prevent="addTime"
                  />
                  <button type="button" class="btn btn-sm btn-outline-secondary" :disabled="!newTime" @click="addTime">
                    Add
                  </button>
                </span>
              </div>
              <div class="form-text">
                Server local time.
                <template v-if="inst.next_restart">Next restart: {{ inst.next_restart }}.</template>
              </div>
            </div>
          </div>
        </div>
      </fieldset>

      <!-- One Save for all of the above -->
      <div class="rsm-savebar card" :class="{ 'rsm-savebar-dirty': dirty }" role="region" aria-label="Save settings">
        <div class="card-body py-2 d-flex flex-wrap align-items-center gap-2">
          <span class="me-auto small" aria-live="polite">
            <template v-if="saving">Saving…</template>
            <template v-else-if="dirty">
              <strong>Unsaved:</strong> {{ pendingSummary }}
              <span v-if="lockedChanges.length" class="text-warning d-block">
                Stop the server to save the game version, template or ports.
              </span>
            </template>
            <span v-else-if="notice" class="text-success">{{ notice }}</span>
            <span v-else class="text-secondary">No unsaved changes.</span>
          </span>
          <button type="button" class="btn btn-outline-secondary" :disabled="!dirty || saving" @click="discard">
            Discard
          </button>
          <button type="submit" class="btn btn-primary" :disabled="!dirty || hasErrors || saving">
            {{ saving ? 'Saving…' : 'Save changes' }}
          </button>
        </div>
        <div v-if="error" class="alert alert-danger rounded-0 rounded-bottom mb-0 py-2 small">{{ error }}</div>
      </div>
    </form>

    <!-- Acts at once, so outside the form -->
    <StoredData
      :id="inst.id"
      :running="running"
      :targets="['mods', 'logs']"
      :server-name="inst.name"
      title="Maintenance"
      @changed="emit('changed')"
    />

    <div class="card border-danger-subtle">
      <div class="card-body d-flex flex-wrap align-items-center gap-2">
        <span class="me-auto">
          <span class="d-block">Delete this server</span>
          <small class="text-secondary">
            Removes its container. You choose whether its data on disk goes too.
          </small>
        </span>
        <button type="button" class="btn btn-outline-danger" @click="emit('delete')">Delete server…</button>
      </div>
    </div>

    <!-- A template change can point the server at a different world: confirm it -->
    <div v-if="confirmTemplate" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Change the template?</h5>
            <button type="button" class="btn-close" aria-label="Close" @click="confirmTemplate = false"></button>
          </div>
          <div class="modal-body">
            <p class="mb-2">
              <strong>{{ templateById(baseline.template_id)?.name || inst.template_name }}</strong>
              → <strong>{{ templateById(draft.template_id)?.name }}</strong>
            </p>
            <p class="small text-secondary">
              The server is rebuilt from the new template the next time it starts.
            </p>
            <div v-if="hiveWarning" class="alert alert-warning py-2 small">⚠ {{ hiveWarning }}</div>
            <div v-if="hasSavedData" class="form-check">
              <input id="backup-before-swap" v-model="backupBeforeSwap" class="form-check-input" type="checkbox" />
              <label class="form-check-label" for="backup-before-swap">
                Back up the saved game first
                <span class="text-secondary">({{ fmtBytes(savedData.size_bytes) }})</span>
              </label>
            </div>
            <p v-if="otherChanges" class="small text-secondary mt-3 mb-0">
              Your other changes ({{ otherChanges }}) are saved with it.
            </p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-outline-secondary" @click="confirmTemplate = false">Cancel</button>
            <button type="button" class="btn btn-primary" @click="save">Save changes</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
fieldset.card {
  min-width: 0;
}

/* A fieldset's legend, drawn as the card's own full-width header. */
legend.card-header {
  float: none;
  width: 100%;
  font-size: 0.875rem;
}

.rsm-rows {
  display: grid;
  gap: 0;
  padding-top: 0.25rem;
  padding-bottom: 0.25rem;
}

/* Label on the left, control on the right; stacked on a narrow screen. */
.rsm-row {
  display: grid;
  grid-template-columns: 12rem minmax(0, 1fr);
  gap: 0.25rem 1rem;
  align-items: start;
  padding: 0.75rem 0;
  border-top: 1px solid var(--bs-border-color);
}

.rsm-row:first-child {
  border-top: 0;
}

.rsm-row-label {
  padding-top: 0.4rem;
  color: var(--bs-secondary-color);
}

.rsm-ports {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 7.5rem));
  gap: 0.5rem;
}

.rsm-ports label {
  display: grid;
  gap: 0.15rem;
}

/* An edited field shows it until it is saved or discarded. */
.rsm-changed {
  border-color: var(--bs-warning);
}

.form-check-input.rsm-changed {
  box-shadow: 0 0 0 0.15rem rgba(var(--bs-warning-rgb), 0.45);
}

.rsm-changed-badge {
  outline: 1px solid var(--bs-warning);
}

.rsm-lock {
  width: 1rem;
  height: 1rem;
  flex: none;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
}

/* The save bar follows you down the form, then settles at its end. */
.rsm-savebar {
  position: sticky;
  bottom: 0.75rem;
  z-index: 5;
  box-shadow: 0 0.25rem 1rem rgba(0, 0, 0, 0.35);
}

.rsm-savebar-dirty {
  border-color: var(--bs-warning);
}

@media (max-width: 991.98px) {
  /* Clear the bottom menu bar. */
  .rsm-savebar {
    bottom: calc(4.5rem + env(safe-area-inset-bottom));
  }
}

@media (max-width: 575.98px) {
  .rsm-row {
    grid-template-columns: minmax(0, 1fr);
  }

  .rsm-row-label {
    padding-top: 0;
  }
}
</style>
