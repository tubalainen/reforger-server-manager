<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const props = defineProps({ id: { type: [String, Number], default: null } })
const router = useRouter()
const editing = computed(() => props.id != null)

const step = ref(1)
const steps = ['Scenario', 'Mods', 'Settings', 'Save']
const error = ref('')
const saving = ref(false)

// The working spec sent to the backend
const spec = reactive({
  name: '',
  description: '',
  scenario_id: '',
  mods: [],
  game_name: 'Arma Reforger Server',
  password: '',
  admin_password: '',
  max_players: 64,
  visible: true,
  cross_platform: true,
  mods_required_by_default: false,
  battleye: true,
  server_max_view_distance: 1600,
  server_min_grass_distance: 0,
  network_view_distance: 1500,
  disable_third_person: false,
  fast_validation: true,
  von_disable_ui: false,
  von_disable_direct_speech_ui: false,
  von_can_transmit_cross_faction: false,
  lobby_player_synchronise: true,
  disable_navmesh_streaming: false,
  disable_server_shutdown: false,
  disable_crash_reporter: false,
  disable_ai: false,
  player_save_time: 120,
  ai_limit: -1,
  slot_reservation_timeout: 60,
  join_queue_max_size: 0,
  persistence_enabled: false,
  auto_save_interval: 10,
  hive_id: 0,
  rcon_password: '',
  rcon_permission: 'admin',
  rcon_max_clients: 16,
})

const showAdvanced = ref(false)

// Display metadata that isn't part of the spec but helps the user
const chosenScenario = ref(null) // {scenario_id, name, from_asset}

// ---- Workshop search (shared by Scenario + Mods steps) --------------------
const search = reactive({ q: '', busy: false, results: [], error: '' })

async function runSearch() {
  if (!search.q.trim()) return
  search.busy = true
  search.error = ''
  try {
    const data = await api(`/api/workshop/search?q=${encodeURIComponent(search.q)}`)
    search.results = data.rows
  } catch (e) {
    search.error = e.message
    search.results = []
  } finally {
    search.busy = false
  }
}

function fmtSize(n) {
  if (!n) return ''
  return n >= 1e9 ? (n / 1e9).toFixed(2) + ' GB' : (n / 1e6).toFixed(1) + ' MB'
}

// ---- Step 1: scenario ------------------------------------------------------
const scenarioPick = reactive({ busy: false, asset: null, error: '' })

async function pickScenarioAsset(row) {
  scenarioPick.busy = true
  scenarioPick.error = ''
  scenarioPick.asset = null
  try {
    const res = await api(`/api/workshop/resolve/${row.id}`)
    scenarioPick.asset = res
    if (!res.asset.scenarios.length) {
      scenarioPick.error = 'This asset exposes no scenarios; pick another or add it as a mod.'
    }
  } catch (e) {
    scenarioPick.error = e.message
  } finally {
    scenarioPick.busy = false
  }
}

function chooseScenario(sc, res) {
  spec.scenario_id = sc.scenario_id
  chosenScenario.value = { ...sc, from_asset: res.asset.name }
  // auto-add the scenario's mod + full dependency tree
  mergeMods(res.mods)
  step.value = 2
}

// ---- Step 2: mods ----------------------------------------------------------
function mergeMods(newMods) {
  const byId = new Map(spec.mods.map((m) => [m.modId, m]))
  for (const m of newMods) byId.set(m.modId, m)
  spec.mods = [...byId.values()]
}

const modAdd = reactive({ busy: false, error: '' })

async function addModByRow(row) {
  modAdd.busy = true
  modAdd.error = ''
  try {
    const res = await api(`/api/workshop/resolve/${row.id}`)
    mergeMods(res.mods)
    if (res.missing.length) {
      modAdd.error = `Added, but ${res.missing.length} dependency(ies) could not be resolved.`
    }
  } catch (e) {
    modAdd.error = e.message
  } finally {
    modAdd.busy = false
  }
}

function removeMod(modId) {
  spec.mods = spec.mods.filter((m) => m.modId !== modId)
}

// ---- Step 3/4: live config preview ----------------------------------------
const preview = ref('')
let previewTimer = null

async function refreshPreview() {
  if (!spec.scenario_id) {
    preview.value = ''
    return
  }
  try {
    const cfg = await api('/api/templates/preview', { method: 'POST', body: spec })
    preview.value = JSON.stringify(cfg, null, 2)
  } catch (e) {
    preview.value = `// ${e.message}`
  }
}

watch(
  () => JSON.stringify(spec),
  () => {
    clearTimeout(previewTimer)
    previewTimer = setTimeout(refreshPreview, 250)
  },
)

// ---- Save ------------------------------------------------------------------
async function save() {
  error.value = ''
  saving.value = true
  try {
    if (editing.value) {
      await api(`/api/templates/${props.id}`, { method: 'PUT', body: spec })
    } else {
      await api('/api/templates', { method: 'POST', body: spec })
    }
    router.push({ name: 'templates' })
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

function downloadJson() {
  const blob = new Blob([preview.value], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = (spec.name || 'config').replace(/[^a-z0-9_-]/gi, '_') + '.json'
  a.click()
  URL.revokeObjectURL(a.href)
}

const canNext = computed(() => {
  if (step.value === 1) return !!spec.scenario_id
  if (step.value === 4) return spec.name.trim().length > 0
  return true
})

onMounted(async () => {
  if (editing.value) {
    try {
      const t = await api(`/api/templates/${props.id}`)
      Object.assign(spec, t.spec)
      spec.name = t.name
      spec.description = t.description
      if (spec.scenario_id) {
        chosenScenario.value = { scenario_id: spec.scenario_id, name: spec.scenario_id }
      }
    } catch (e) {
      error.value = e.message
    }
  }
  refreshPreview()
})
</script>

<template>
  <div class="container">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">{{ editing ? 'Edit template' : 'New template' }}</h1>
      <button class="btn btn-outline-secondary btn-sm" @click="router.push({ name: 'templates' })">
        Cancel
      </button>
    </div>

    <!-- Stepper -->
    <ul class="nav nav-pills mb-4">
      <li v-for="(label, i) in steps" :key="label" class="nav-item">
        <button
          class="nav-link"
          :class="{ active: step === i + 1 }"
          :disabled="i + 1 > 1 && !spec.scenario_id"
          @click="step = i + 1"
        >
          {{ i + 1 }}. {{ label }}
        </button>
      </li>
    </ul>

    <div v-if="error" class="alert alert-danger py-2">{{ error }}</div>

    <div class="row">
      <div class="col-lg-7">
        <!-- STEP 1: SCENARIO -->
        <div v-show="step === 1">
          <p class="text-secondary">
            Search the Workshop and pick a scenario. Its mod and all dependencies are added
            automatically.
          </p>
          <div class="input-group mb-3">
            <input
              v-model="search.q"
              class="form-control"
              placeholder="Search scenarios, e.g. Conflict, Overthrow…"
              @keyup.enter="runSearch"
            />
            <button class="btn btn-primary" :disabled="search.busy" @click="runSearch">
              {{ search.busy ? 'Searching…' : 'Search' }}
            </button>
          </div>
          <div v-if="search.error" class="alert alert-warning py-2 small">{{ search.error }}</div>

          <div class="list-group mb-3">
            <button
              v-for="row in search.results"
              :key="row.id"
              class="list-group-item list-group-item-action text-start"
              @click="pickScenarioAsset(row)"
            >
              <div class="d-flex justify-content-between">
                <span class="fw-semibold">{{ row.name }}</span>
                <small class="text-secondary">{{ fmtSize(row.size) }}</small>
              </div>
              <small class="text-secondary">by {{ row.author }} · v{{ row.version }}</small>
            </button>
          </div>

          <div v-if="scenarioPick.busy" class="text-secondary">Loading scenarios…</div>
          <div v-if="scenarioPick.error" class="alert alert-info py-2 small">
            {{ scenarioPick.error }}
          </div>
          <div v-if="scenarioPick.asset" class="card">
            <div class="card-body">
              <h2 class="h6">{{ scenarioPick.asset.asset.name }} — scenarios</h2>
              <div class="list-group">
                <button
                  v-for="sc in scenarioPick.asset.asset.scenarios"
                  :key="sc.scenario_id"
                  class="list-group-item list-group-item-action"
                  @click="chooseScenario(sc, scenarioPick.asset)"
                >
                  <div class="fw-semibold">{{ sc.name }}</div>
                  <small class="text-secondary">
                    {{ sc.game_mode }} · {{ sc.player_count }} players
                    · adds {{ scenarioPick.asset.mods.length }} mod(s),
                    {{ fmtSize(scenarioPick.asset.total_size) }}
                  </small>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- STEP 2: MODS -->
        <div v-show="step === 2">
          <p class="text-secondary">
            Additional mods (dependencies resolve automatically). The scenario's own mods are
            already included.
          </p>
          <div class="input-group mb-2">
            <input
              v-model="search.q"
              class="form-control"
              placeholder="Search mods to add…"
              @keyup.enter="runSearch"
            />
            <button class="btn btn-primary" :disabled="search.busy" @click="runSearch">
              {{ search.busy ? 'Searching…' : 'Search' }}
            </button>
          </div>
          <div v-if="modAdd.error" class="alert alert-info py-2 small">{{ modAdd.error }}</div>
          <div class="list-group mb-4">
            <div
              v-for="row in search.results"
              :key="row.id"
              class="list-group-item d-flex justify-content-between align-items-center"
            >
              <div>
                <div class="fw-semibold">{{ row.name }}</div>
                <small class="text-secondary">v{{ row.version }} · {{ fmtSize(row.size) }}</small>
              </div>
              <button class="btn btn-sm btn-outline-primary" :disabled="modAdd.busy"
                @click="addModByRow(row)">Add</button>
            </div>
          </div>

          <h2 class="h6">Selected mods ({{ spec.mods.length }})</h2>
          <div v-if="!spec.mods.length" class="text-secondary small">None yet.</div>
          <ul class="list-group">
            <li
              v-for="m in spec.mods"
              :key="m.modId"
              class="list-group-item d-flex justify-content-between align-items-center py-2"
            >
              <span>
                {{ m.name || m.modId }}
                <small class="text-secondary">v{{ m.version || '—' }} · {{ m.modId }}</small>
              </span>
              <button class="btn btn-sm btn-outline-danger" @click="removeMod(m.modId)">✕</button>
            </li>
          </ul>
        </div>

        <!-- STEP 3: SETTINGS -->
        <div v-show="step === 3">
          <div class="row g-3">
            <div class="col-12">
              <label class="form-label">Server name (in-game browser)</label>
              <input v-model="spec.game_name" class="form-control" />
            </div>
            <div class="col-md-6">
              <label class="form-label">Join password</label>
              <input v-model="spec.password" class="form-control" placeholder="(none)" />
            </div>
            <div class="col-md-6">
              <label class="form-label">Admin password</label>
              <input v-model="spec.admin_password" class="form-control" placeholder="(none)" />
            </div>
            <div class="col-md-4">
              <label class="form-label">Max players</label>
              <input v-model.number="spec.max_players" type="number" min="1" max="256" class="form-control" />
            </div>
            <div class="col-md-4">
              <label class="form-label">Server view distance</label>
              <input v-model.number="spec.server_max_view_distance" type="number" class="form-control" />
            </div>
            <div class="col-md-4">
              <label class="form-label">Network view distance</label>
              <input v-model.number="spec.network_view_distance" type="number" class="form-control" />
            </div>
            <div class="col-12">
              <label class="form-label">RCON password <small class="text-secondary">(optional)</small></label>
              <input v-model="spec.rcon_password" class="form-control" placeholder="(RCON disabled)" />
            </div>
            <div class="col-12 d-flex gap-4">
              <div class="form-check">
                <input id="visible" v-model="spec.visible" class="form-check-input" type="checkbox" />
                <label for="visible" class="form-check-label">Public (server browser)</label>
              </div>
              <div class="form-check">
                <input id="cross" v-model="spec.cross_platform" class="form-check-input" type="checkbox" />
                <label for="cross" class="form-check-label">Cross-platform</label>
              </div>
              <div class="form-check">
                <input id="be" v-model="spec.battleye" class="form-check-input" type="checkbox" />
                <label for="be" class="form-check-label">BattlEye</label>
              </div>
              <div class="form-check">
                <input id="tp" v-model="spec.disable_third_person" class="form-check-input" type="checkbox" />
                <label for="tp" class="form-check-label">First-person only</label>
              </div>
            </div>
          </div>

          <button
            class="btn btn-link px-0 mt-3"
            @click="showAdvanced = !showAdvanced"
          >
            {{ showAdvanced ? '▾ Hide' : '▸ Show' }} advanced settings
          </button>

          <div v-show="showAdvanced" class="row g-3 border-top pt-3">
            <div class="col-md-4">
              <label class="form-label">Min grass distance</label>
              <input v-model.number="spec.server_min_grass_distance" type="number" min="0" max="150" class="form-control" />
            </div>
            <div class="col-md-4">
              <label class="form-label">Player save interval (s)</label>
              <input v-model.number="spec.player_save_time" type="number" min="0" class="form-control" />
            </div>
            <div class="col-md-4">
              <label class="form-label">AI limit <small class="text-secondary">(-1 = unlimited)</small></label>
              <input v-model.number="spec.ai_limit" type="number" min="-1" class="form-control" />
            </div>
            <div class="col-md-4">
              <label class="form-label">Slot reservation timeout (s)</label>
              <input v-model.number="spec.slot_reservation_timeout" type="number" min="5" max="300" class="form-control" />
            </div>
            <div class="col-md-4">
              <label class="form-label">Join queue max size <small class="text-secondary">(0 = off)</small></label>
              <input v-model.number="spec.join_queue_max_size" type="number" min="0" max="50" class="form-control" />
            </div>
            <div class="col-12">
              <div class="fw-semibold small text-secondary mb-1">VON (voice)</div>
              <div class="d-flex gap-4 flex-wrap">
                <div class="form-check">
                  <input id="von1" v-model="spec.von_disable_ui" class="form-check-input" type="checkbox" />
                  <label for="von1" class="form-check-label">Disable VON UI</label>
                </div>
                <div class="form-check">
                  <input id="von2" v-model="spec.von_disable_direct_speech_ui" class="form-check-input" type="checkbox" />
                  <label for="von2" class="form-check-label">Disable direct-speech UI</label>
                </div>
                <div class="form-check">
                  <input id="von3" v-model="spec.von_can_transmit_cross_faction" class="form-check-input" type="checkbox" />
                  <label for="von3" class="form-check-label">Cross-faction VON</label>
                </div>
              </div>
            </div>
            <div class="col-12">
              <div class="fw-semibold small text-secondary mb-1">Operating</div>
              <div class="d-flex gap-4 flex-wrap">
                <div class="form-check">
                  <input id="op1" v-model="spec.fast_validation" class="form-check-input" type="checkbox" />
                  <label for="op1" class="form-check-label">Fast validation</label>
                </div>
                <div class="form-check">
                  <input id="op2" v-model="spec.lobby_player_synchronise" class="form-check-input" type="checkbox" />
                  <label for="op2" class="form-check-label">Lobby player sync</label>
                </div>
                <div class="form-check">
                  <input id="op3" v-model="spec.disable_navmesh_streaming" class="form-check-input" type="checkbox" />
                  <label for="op3" class="form-check-label">Disable navmesh streaming</label>
                </div>
                <div class="form-check">
                  <input id="op4" v-model="spec.disable_server_shutdown" class="form-check-input" type="checkbox" />
                  <label for="op4" class="form-check-label">Disable auto-shutdown</label>
                </div>
                <div class="form-check">
                  <input id="op5" v-model="spec.disable_crash_reporter" class="form-check-input" type="checkbox" />
                  <label for="op5" class="form-check-label">Disable crash reporter</label>
                </div>
                <div class="form-check">
                  <input id="op6" v-model="spec.disable_ai" class="form-check-input" type="checkbox" />
                  <label for="op6" class="form-check-label">Disable AI</label>
                </div>
                <div class="form-check">
                  <input id="op7" v-model="spec.mods_required_by_default" class="form-check-input" type="checkbox" />
                  <label for="op7" class="form-check-label">Mods required by default</label>
                </div>
              </div>
            </div>

            <div class="col-12">
              <div class="fw-semibold small text-secondary mb-1">Persistence (save games)</div>
              <div class="row g-2 align-items-end">
                <div class="col-auto">
                  <div class="form-check mb-2">
                    <input id="persist" v-model="spec.persistence_enabled" class="form-check-input" type="checkbox" />
                    <label for="persist" class="form-check-label">Enable persistence</label>
                  </div>
                </div>
                <div class="col-6 col-md-3">
                  <label class="form-label small">Auto-save interval (min)</label>
                  <input v-model.number="spec.auto_save_interval" type="number" min="0" max="60"
                    class="form-control" :disabled="!spec.persistence_enabled" />
                </div>
                <div class="col-6 col-md-3">
                  <label class="form-label small">Hive ID</label>
                  <input v-model.number="spec.hive_id" type="number" min="0" max="16383"
                    class="form-control" :disabled="!spec.persistence_enabled" />
                </div>
              </div>
            </div>

            <div class="col-12">
              <div class="fw-semibold small text-secondary mb-1">RCON <small>(only used when a password is set on the previous screen)</small></div>
              <div class="row g-2">
                <div class="col-6 col-md-4">
                  <label class="form-label small">Permission</label>
                  <select v-model="spec.rcon_permission" class="form-select">
                    <option value="admin">admin</option>
                    <option value="monitor">monitor</option>
                  </select>
                </div>
                <div class="col-6 col-md-4">
                  <label class="form-label small">Max clients</label>
                  <input v-model.number="spec.rcon_max_clients" type="number" min="1" max="16" class="form-control" />
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- STEP 4: SAVE -->
        <div v-show="step === 4">
          <div class="mb-3">
            <label class="form-label">Template name</label>
            <input v-model="spec.name" class="form-control" placeholder="e.g. Conflict Everon (stable)" />
          </div>
          <div class="mb-3">
            <label class="form-label">Description <small class="text-secondary">(optional)</small></label>
            <input v-model="spec.description" class="form-control" />
          </div>
          <div class="d-flex gap-2">
            <button class="btn btn-primary" :disabled="!canNext || saving" @click="save">
              {{ saving ? 'Saving…' : editing ? 'Save changes' : 'Save template' }}
            </button>
            <button class="btn btn-outline-secondary" @click="downloadJson">Download config.json</button>
          </div>
        </div>

        <!-- Nav buttons -->
        <div class="d-flex justify-content-between mt-4">
          <button class="btn btn-outline-secondary" :disabled="step === 1" @click="step--">
            ← Back
          </button>
          <button v-if="step < 4" class="btn btn-primary" :disabled="!canNext" @click="step++">
            Next →
          </button>
        </div>
      </div>

      <!-- Live config.json preview -->
      <div class="col-lg-5">
        <div class="card position-sticky" style="top: 1rem">
          <div class="card-header d-flex justify-content-between align-items-center py-2">
            <span class="small fw-semibold">config.json preview</span>
            <span v-if="chosenScenario" class="badge text-bg-secondary">scenario set</span>
          </div>
          <pre
            class="card-body bg-black text-light small mb-0 rounded-bottom"
            style="max-height: 70vh; overflow: auto; white-space: pre-wrap"
          >{{ preview || '// pick a scenario to see the config' }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>
