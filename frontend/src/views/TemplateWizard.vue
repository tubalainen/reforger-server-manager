<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, clientId } from '../api'
import { formatBytes } from '../format'

// CodeMirror is ~130 KB gzipped and only needed once someone clicks "Edit JSON",
// which most users never will — so it gets its own chunk instead of riding along
// in the bundle every page load (#29).
const JsonEditor = defineAsyncComponent(() => import('../components/JsonEditor.vue'))
import {
  MODS_FILE_FORMAT,
  applyModTemplate,
  extractModIds,
  normalizeMods,
  requiredBy,
  stillRequiredWithoutExplicit,
  clearScenarioMods,
  mergeResolved,
  orderedMods,
  partitionOrder,
  sortModsByAdded,
  sortModsByName,
  applyOrderIds,
  dependencyViolations,
  moveMod,
  movedMods,
  reorderMods,
  topoOrder,
} from '../mods'
import {
  ADMIN_LIMIT,
  addAdmins,
  addNotice,
  addPlayers,
  idKind,
  removeAdmin,
  removePlayer,
} from '../players'
import {
  applyMerge,
  coerceValue,
  convertQuotedNumbers,
  countKeys,
  format as formatHeader,
  fromRows,
  parsePaste,
  previewMerge,
  toRows,
} from '../missionHeader'
import { GROUPS, defaultValueFor, describeKey, searchKeys } from '../missionHeaderKeys'

const props = defineProps({ id: { type: [String, Number], default: null } })
const router = useRouter()
const editing = computed(() => props.id != null)

const step = ref(1)
const steps = ['Scenario', 'Mods', 'Settings', 'Save']
const error = ref('')
const saving = ref(false)

// ---- Edit lock (#102) ------------------------------------------------------
// Editing an existing template takes a lock so two sessions can't race each
// other to Save. Acquire on open, heartbeat every 30s (the lock expires after
// 90 without one), release on leave. `locked` = someone else holds it: the
// page stays viewable but Save is disabled — and since the heartbeat keeps
// trying, it unlocks by itself once the other editor leaves.
const locked = ref(false)
let lockTimer = null

async function heartbeatLock() {
  try {
    await api(`/api/templates/${props.id}/lock`, { method: 'POST' })
    locked.value = false
  } catch (e) {
    if (e.status === 423) locked.value = true
    // anything else (network blip): keep the current state, next beat retries
  }
}

function releaseLock() {
  if (!editing.value || locked.value) return
  // keepalive lets the request outlive a closing tab; best-effort by design
  fetch(`/api/templates/${props.id}/lock`, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: { 'X-Client-Id': clientId },
    keepalive: true,
  })
}

// The working spec sent to the backend
const spec = reactive({
  name: '',
  description: '',
  scenario_id: '',
  scenario_name: '',
  scenario_player_count: null,
  mods: [],
  game_name: 'Arma Reforger Server',
  password: '',
  admin_password: '',
  // Player access lists (#154). Declared here so a NEW template carries them
  // too — on edit they arrive with the loaded spec.
  admins: [],
  player_whitelist: [],
  player_ban_list: [],
  max_players: 64,
  visible: true,
  cross_platform: true,
  mods_required_by_default: false,
  battleye: true,
  server_max_view_distance: 1600,
  server_min_grass_distance: 50,
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
  save_retention: 10,
  load_session_save: true,
  keep_session_save: false,
  hive_id: 0,
  rcon_password: '',
  rcon_permission: 'admin',
  rcon_max_clients: 16,
  // Mission header overrides (#162). Declared here so a NEW template carries the
  // field too; on edit it arrives with the loaded spec.
  mission_header: {},
  // Hand-edited keys the wizard doesn't model, as a merge patch the backend
  // re-applies over every render (#29). Set by the JSON editor's Apply.
  extras: {},
  launch: {
    // New templates cap the server at 60 FPS by default (#143). Clear the field
    // to run uncapped. Existing templates keep whatever they saved: the loader
    // merges the stored launch over these defaults, so an explicit value wins.
    max_fps: 60,
    network_dynamic_simulation: null,
    spatial_map_resolution: null,
    staggering_budget: null,
    streaming_budget: null,
    streams_delta: null,
    auto_reload_scenario: null,
    rpl_timeout_ms: null,
    freeze_check: null,
    freeze_check_mode: null,
    debugger_address: null,
    debugger_port: null,
    load_session_save: null,
    short_worker_count: null,
    long_worker_count: null,
    verify_and_repair_addons: false,
    auto_shutdown: false,
    log_voting: false,
    ai_partial_sim: false,
    force_recreate_database: false,
    disable_shaders_build: false,
    generate_shaders: false,
    rpl_encode_as_long_jobs: false,
    force_disable_night_grain: false,
    no_backend: false,
    extra_args: '',
  },
})

const showAdvanced = ref(false)
const showLaunch = ref(false)
const showAccess = ref(false)
const showMissionHeader = ref(false)

// ---- Player access: admins, whitelist, ban list (#154) ---------------------
const adminInput = ref('')
const adminNotice = ref('')
const whitelistInput = reactive({ id: '', name: '' })
const whitelistNotice = ref('')
const banInput = reactive({ id: '', name: '', reason: '' })
const banNotice = ref('')

// The game applies at most 20 admins. A template can arrive over the limit from
// an import or a hand-edit, so say so rather than silently truncating.
const adminsOverLimit = computed(() => spec.admins.length > ADMIN_LIMIT)

// Shown next to the collapsed "player access" toggle. The section starts closed,
// so without this a whitelist-only server looks exactly like an open one.
const accessSummary = computed(() => {
  const parts = []
  const { admins, player_whitelist: white, player_ban_list: banned } = spec
  if (admins.length) parts.push(`${admins.length} admin${admins.length === 1 ? '' : 's'}`)
  if (white.length) parts.push(`whitelist-only (${white.length})`)
  if (banned.length) parts.push(`${banned.length} banned`)
  return parts.join(' · ')
})

function addAdminIds() {
  const result = addAdmins(spec.admins, adminInput.value)
  spec.admins = result.admins
  adminNotice.value = addNotice(result)
  // Keep what could not be added in the box so it can be corrected in place,
  // and clear it when everything landed (#104).
  adminInput.value = [...result.invalid, ...result.overflow].join(' ')
}

function dropAdmin(id) {
  spec.admins = removeAdmin(spec.admins, id)
  adminNotice.value = ''
}

function addWhitelistPlayers() {
  const result = addPlayers(spec.player_whitelist, whitelistInput.id, {
    name: whitelistInput.name,
  })
  spec.player_whitelist = result.players
  whitelistNotice.value = addNotice(result)
  whitelistInput.id = result.invalid.join(' ')
  if (result.added.length) whitelistInput.name = ''
}

function dropWhitelistPlayer(id) {
  spec.player_whitelist = removePlayer(spec.player_whitelist, id)
  whitelistNotice.value = ''
}

function addBannedPlayers() {
  const result = addPlayers(spec.player_ban_list, banInput.id, {
    name: banInput.name,
    reason: banInput.reason,
    withReason: true,
  })
  spec.player_ban_list = result.players
  banNotice.value = addNotice(result)
  banInput.id = result.invalid.join(' ')
  if (result.added.length) {
    banInput.name = ''
    banInput.reason = ''
  }
}

function dropBannedPlayer(id) {
  spec.player_ban_list = removePlayer(spec.player_ban_list, id)
  banNotice.value = ''
}

// ---- Mission header overrides (#162) ---------------------------------------
// Two views over one object: a row per setting, and the raw JSON for the nested
// blocks a mod's header class brings (ACE nests a whole settings tree). The
// object in `spec.mission_header` is the source of truth; rows are a projection
// of it that is rebuilt whenever it changes from somewhere else — the JSON view,
// the paste dialog, or an Edit JSON apply.
const headerView = ref('rows') // 'rows' | 'json'
const headerRows = ref([])
const headerJson = ref('{}')
const headerJsonError = ref('')
const headerPick = reactive({ open: false, query: '' })

const missionHeaderCount = computed(() => countKeys(spec.mission_header))

watch(
  () => JSON.stringify(spec.mission_header),
  () => {
    if (JSON.stringify(fromRows(headerRows.value)) !== JSON.stringify(spec.mission_header)) {
      headerRows.value = toRows(spec.mission_header)
    }
    if (headerView.value !== 'json') headerJson.value = formatHeader(spec.mission_header)
  },
  { immediate: true },
)

function commitHeaderRows() {
  spec.mission_header = fromRows(headerRows.value)
}

function addHeaderRow(entry, rawKey) {
  const key = entry?.key ?? String(rawKey ?? '').trim()
  if (!key || headerRows.value.some((row) => row.key === key)) return
  const value = entry ? defaultValueFor(entry) : ''
  headerRows.value.push({ key, value, kind: entry?.kind ?? 'string' })
  headerPick.query = ''
  headerPick.open = false
  commitHeaderRows()
}

function dropHeaderRow(index) {
  headerRows.value.splice(index, 1)
  commitHeaderRows()
}

function changeHeaderRowKind(row, kind) {
  row.kind = kind
  row.value = coerceValue(row.value, kind)
  commitHeaderRows()
}

const headerMatches = computed(() =>
  searchKeys(headerPick.query, headerRows.value.map((row) => row.key)).slice(0, 8),
)

const headerGroupLabel = Object.fromEntries(GROUPS.map((g) => [g.id, g.label]))

// A setting that only takes effect once another one is switched on — the time
// of day settings need m_bOverrideScenarioTimeAndWeather. Surfaced as a prompt
// rather than added behind the user's back.
const headerMissingPrereqs = computed(() => {
  const present = new Set(headerRows.value.map((row) => row.key))
  const missing = []
  for (const row of headerRows.value) {
    const needs = describeKey(row.key)?.requires
    if (!needs || missing.includes(needs)) continue
    if (!present.has(needs) || spec.mission_header[needs] !== true) missing.push(needs)
  }
  return missing
})

function addPrereq(key) {
  const entry = describeKey(key)
  const existing = headerRows.value.find((row) => row.key === key)
  if (existing) {
    existing.value = true
    existing.kind = 'boolean'
  } else {
    headerRows.value.push({ key, value: true, kind: entry?.kind ?? 'boolean' })
  }
  commitHeaderRows()
}

function describeRow(key) {
  return describeKey(key)
}

function nestedSummary(value) {
  const n = countKeys(value)
  return `{…} ${n} setting${n === 1 ? '' : 's'}`
}

function openHeaderJson() {
  headerJson.value = formatHeader(spec.mission_header)
  headerJsonError.value = ''
  headerView.value = 'json'
}

function applyHeaderJson() {
  const result = parsePaste(headerJson.value)
  if (!result.ok) {
    headerJsonError.value = result.error
    return
  }
  headerJsonError.value = ''
  spec.mission_header = result.header
  headerJson.value = formatHeader(result.header)
}

// ---- the paste dialog -------------------------------------------------------
// The reason this exists: a mission header is nearly always copied from
// somewhere else — a forum post, AMP's brace-less box, someone's config.json —
// and it arrives with ragged indentation, comments, trailing commas or a
// truncated tail. Repair it, say what was repaired, show what it would change,
// and only then insert. Nothing is ever spliced into the config as text.
const paste = reactive({
  open: false,
  text: '',
  mode: 'merge', // 'merge' | 'replace'
  error: '',
  repairs: [],
  header: null,
  quoted: [],
  convertQuoted: false,
})

function openPaste() {
  Object.assign(paste, {
    open: true, text: '', mode: 'merge', error: '', repairs: [],
    header: null, quoted: [], convertQuoted: false,
  })
}

// Re-parsed as the user types, so the repair list and the preview are visible
// before anything is committed.
watch(
  () => paste.text,
  (text) => {
    if (!text.trim()) {
      Object.assign(paste, { error: '', repairs: [], header: null, quoted: [] })
      return
    }
    const result = parsePaste(text)
    if (!result.ok) {
      Object.assign(paste, { error: result.error, repairs: [], header: null, quoted: [] })
      return
    }
    Object.assign(paste, {
      error: '',
      repairs: result.repairs,
      header: result.header,
      quoted: result.quotedNumberKeys,
    })
  },
)

const pasteHeader = computed(() =>
  paste.header && paste.convertQuoted
    ? convertQuotedNumbers(paste.header, paste.quoted)
    : paste.header,
)

const pastePreview = computed(() =>
  pasteHeader.value ? previewMerge(spec.mission_header, pasteHeader.value) : null,
)

function confirmPaste() {
  if (!pasteHeader.value) return
  spec.mission_header = applyMerge(spec.mission_header, pasteHeader.value, paste.mode)
  headerJson.value = formatHeader(spec.mission_header)
  paste.open = false
}

// Launch-parameter field definitions, rendered generically to keep the
// template compact. type: 'num' | 'text' | 'switch' | 'select'.
const launchNumFields = [
  ['max_fps', 'Max FPS'],
  ['auto_reload_scenario', 'Auto-reload scenario (s)'],
  ['spatial_map_resolution', 'Spatial map resolution (100–1000)'],
  ['network_dynamic_simulation', 'Network dynamic simulation'],
  ['staggering_budget', 'Staggering budget'],
  ['streaming_budget', 'Streaming budget'],
  ['streams_delta', 'Streams delta'],
  ['rpl_timeout_ms', 'RPL timeout (ms)'],
  ['freeze_check', 'Freeze check (s)'],
  ['short_worker_count', 'Short worker count'],
  ['long_worker_count', 'Long worker count'],
  ['debugger_port', 'Debugger port'],
]
const launchSwitchFields = [
  ['verify_and_repair_addons', 'Verify & repair addons'],
  ['auto_shutdown', 'Auto shutdown'],
  ['log_voting', 'Log voting'],
  ['ai_partial_sim', 'AI partial simulation'],
  ['force_recreate_database', 'Force recreate database'],
  ['disable_shaders_build', 'Disable shaders generation'],
  ['generate_shaders', 'Force generate shaders'],
  ['rpl_encode_as_long_jobs', 'RPL encode as long jobs'],
  ['force_disable_night_grain', 'Force disable night grain'],
  ['no_backend', 'No backend'],
]

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

const fmtSize = (n) => formatBytes(n, { base: 1000, empty: '' })

// ---- Step 1: scenario ------------------------------------------------------
// Scenarios AND terrains are pickable here: a terrain (map) publishes playable
// scenarios of its own, frequently several — the Visingsö map ships a Conflict
// and a Game Master scenario. Whatever the asset is, its scenarios are listed and
// the user chooses one, because a server config holds exactly one scenarioId.
const scenarioPick = reactive({ busy: false, asset: null, error: '' })

const assetKindBadge = {
  scenario: 'text-bg-info',
  terrain: 'text-bg-success',
  addon: 'text-bg-secondary',
}

const pickedScenarios = computed(() => scenarioPick.asset?.asset?.scenarios || [])
// `kind` is classified from the Workshop's tags by the backend (asset_kind).
const pickedAssetKind = computed(() => scenarioPick.asset?.asset?.kind || 'asset')

async function pickScenarioAsset(row) {
  scenarioPick.busy = true
  scenarioPick.error = ''
  scenarioPick.asset = null
  try {
    const res = await api(`/api/workshop/resolve/${row.id}`)
    scenarioPick.asset = res
    if (!res.asset.scenarios.length) {
      scenarioPick.error =
        `"${res.asset.name}" publishes no scenarios of its own — pick a scenario or a ` +
        'terrain here, and add this one as a mod on the next step.'
    }
  } catch (e) {
    scenarioPick.error = e.message
  } finally {
    scenarioPick.busy = false
  }
}

// The mission file name out of a scenarioId — the fallback display name for
// templates saved before the name was persisted (#59).
function scenarioFileName(id) {
  const m = /([^/\\]+)\.conf$/i.exec(id || '')
  return m ? m[1] : ''
}

const scenarioDisplayName = computed(
  () => spec.scenario_name || scenarioFileName(spec.scenario_id) || spec.scenario_id,
)
// The Workshop mod backing the current scenario (none for base-game scenarios).
const scenarioSourceMod = computed(() => spec.mods.find((m) => m.from_scenario) || null)

// Mods that would leave with the current scenario: its own mods plus the
// dependencies nothing else needs (user-added mods and shared deps survive).
const scenarioDroppedMods = computed(() => {
  const current = normalizeMods(spec.mods)
  const kept = new Set(clearScenarioMods(current).map((m) => m.modId))
  return current.filter((m) => !kept.has(m.modId))
})

// Failsafes (#59): replacing or removing the scenario asks first, spelling out
// which mods go with it.
const replacePrompt = ref(null) // { sc, res } awaiting confirmation
const removeScenarioPrompt = ref(false)

function chooseScenario(sc, res) {
  if (spec.scenario_id && sc.scenario_id !== spec.scenario_id) {
    replacePrompt.value = { sc, res }
    return
  }
  applyScenario(sc, res)
}

// A Workshop player count we are willing to act on (the field accepts 1–256).
function usablePlayerCount(n) {
  return Number.isInteger(n) && n >= 1 && n <= 256 ? n : null
}

function applyScenario(sc, res) {
  spec.scenario_id = sc.scenario_id
  spec.scenario_name = sc.name || ''
  // Size the server for the scenario the Workshop declares it for (#65) —
  // 64 slots on a 12-player co-op scenario is nobody's intent. The Settings
  // step keeps the field editable and offers this value back as a reset.
  spec.scenario_player_count = usablePlayerCount(sc.player_count)
  if (spec.scenario_player_count) {
    spec.max_players = spec.scenario_player_count
    maxPlayersNotice.value = spec.scenario_player_count
  }
  // Swap in this scenario's mod + full dependency tree, dropping the previous
  // scenario's mods (but keeping any also required by a user-added mod).
  spec.mods = mergeResolved(clearScenarioMods(normalizeMods(spec.mods)), res, {
    fromScenario: true,
  })
  replacePrompt.value = null
  step.value = 2
}

function removeScenario() {
  spec.mods = clearScenarioMods(normalizeMods(spec.mods))
  spec.scenario_id = ''
  spec.scenario_name = ''
  // max_players is left alone: it is the user's setting now, scenario or not.
  spec.scenario_player_count = null
  maxPlayersNotice.value = null
  removeScenarioPrompt.value = false
}

// Set when picking a scenario changed max_players, so the Settings step can say
// so rather than silently moving a number the user may have typed earlier.
const maxPlayersNotice = ref(null)

const maxPlayersOverridden = computed(
  () => !!spec.scenario_player_count && spec.max_players !== spec.scenario_player_count,
)

function resetMaxPlayers() {
  spec.max_players = spec.scenario_player_count
}

// ---- Step 2: mods ----------------------------------------------------------
const modAdd = reactive({ busy: false, error: '' })
const modNotice = ref('')
const modsFileInput = ref(null)

// One field adds both ways: paste Workshop ids / URLs (several, comma-separated,
// #104) to add them directly, or type free text to search. The button label
// follows suit.
const searchModIds = computed(() => extractModIds(search.q))
const searchIsModId = computed(() => searchModIds.value.length > 0)

const byModId = (id) => spec.mods.find((m) => m.modId === id)

// Mods listed before something they require. Since #164 the list is one
// reorderable sequence, so this is a state the user can put it in — it is
// pointed out, never corrected behind their back.
const depOrderWarnings = computed(() => dependencyViolations(spec.mods))

// Is `m` required (transitively) by the mod backing the selected scenario?
function requiredByScenario(m) {
  return requiredBy(spec.mods, m.modId).some((r) => r.from_scenario)
}

// A user-added mod that publishes its own scenario(s) but isn't the selected
// one — i.e. a *second* scenario slipped in as a mod (#69). A Reforger server
// runs a single scenario, so this is flagged, not silently treated as an addon.
function isExtraScenarioMod(m) {
  return m.explicit && m.provides_scenarios && !m.from_scenario && !requiredByScenario(m)
}

const extraScenarioMods = computed(() => spec.mods.filter(isExtraScenarioMod))

// Badge per mod (#69): "scenario" = the mod providing the selected scenario,
// "scenario dependency" = needed for that scenario to work, "scenario mod" =
// an added mod that carries its own (unused) scenario, "addon" = an extra the
// user chose on top, "dependency" = pulled in by an addon.
function modBadge(m) {
  if (m.from_scenario) {
    return {
      text: 'scenario',
      cls: 'text-bg-info',
      title: 'Provides the selected scenario — change the scenario on step 1 to remove it',
    }
  }
  if (requiredByScenario(m)) {
    return {
      text: 'scenario dependency',
      cls: 'text-bg-secondary',
      title: "Required by the scenario's mod — the scenario won't work without it",
    }
  }
  if (isExtraScenarioMod(m)) {
    return {
      text: 'scenario mod',
      cls: 'text-bg-warning',
      title:
        'This mod publishes its own scenario(s). A server runs only the scenario picked on ' +
        'step 1 — this is enabled for its content, not as a second playable scenario. To ' +
        'play its scenario, set it on step 1 instead.',
    }
  }
  if (m.explicit) {
    return {
      text: 'addon',
      cls: 'text-bg-primary',
      title: 'Extra mod chosen on top of the scenario — not needed by the scenario itself',
    }
  }
  return {
    text: 'dependency',
    cls: 'text-bg-secondary',
    title: 'Pulled in automatically because an enabled mod requires it',
  }
}

// Names of the explicit mods that pulled in a given dependency (for its tooltip).
function requiredByNames(id) {
  return requiredBy(spec.mods, id)
    .map((m) => m.name || m.modId)
    .join(', ')
}

const isEnabled = (id) => spec.mods.some((m) => m.modId === id && m.explicit)

// ---- Workshop metadata hydration (issues #60, #69) ---------------------------
// Templates saved before v0.22 (and mods imported from a config.json or an
// older mods JSON) carry no published-version history, no dependency edges and
// no scenario flag: the lock picker showed only "latest" (#60) and every mod
// was badged "added" — even the one backing the scenario (#69). Refetch each
// mod's Workshop detail in the background whenever the wizard loads an
// existing mod list and fill in what's missing; failures are ignored (the UI
// degrades to what the saved template already had).
const hydratingVersions = ref(false)

async function hydrateVersionHistories() {
  const ids = spec.mods.map((m) => m.modId)
  if (!ids.length) return
  hydratingVersions.value = true
  const queue = [...ids]
  async function worker() {
    while (queue.length) {
      const id = queue.shift()
      try {
        const asset = await api(`/api/workshop/asset/${id}`)
        // Look the mod up again: the list may have changed while fetching.
        const mod = spec.mods.find((m) => m.modId === id)
        if (!mod) continue
        if (asset.versions?.length) mod.versions = asset.versions
        if (!mod.name) mod.name = asset.name
        // Flag mods that publish their own scenario(s) so a second scenario
        // added as a mod is called out rather than badged a plain addon (#69).
        mod.provides_scenarios = !!asset.scenarios?.length
        // Imported lists have no graph edges — take the asset's direct deps
        // so the "scenario dependency" / "required by" markers work (#69).
        if (!mod.dependencies?.length && asset.dependencies?.length) {
          mod.dependencies = asset.dependencies.map((d) => d.id).filter(Boolean)
        }
        // Recognise the mod backing the current scenario when the flag was
        // never stored (config.json imports); trust an existing flag if set.
        const ownScenario = asset.scenarios?.find(
          (sc) => sc.scenario_id === spec.scenario_id,
        )
        if (
          spec.scenario_id &&
          !spec.mods.some((m2) => m2.from_scenario) &&
          ownScenario
        ) {
          mod.from_scenario = true
        }
        // Learn the scenario's declared player count for templates saved before
        // it was stored (#65). Only the hint is filled in — max_players is the
        // user's saved setting and is never rewritten behind their back.
        if (ownScenario && !spec.scenario_player_count) {
          spec.scenario_player_count = usablePlayerCount(ownScenario.player_count)
        }
      } catch {
        /* Workshop unreachable or mod unlisted — keep the row as-is */
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, ids.length) }, worker))
  hydratingVersions.value = false
}

// ---- Per-mod version lock (issue #60) ---------------------------------------
// `m.version` = null means "follow the Workshop's latest release" (config.json
// omits the version). The picker lists the published history; a locked version
// no longer in that list (imported/older template) stays selectable.
function lockOptions(m) {
  const opts = [...(m.versions || [])]
  if (m.version && !opts.includes(m.version)) opts.unshift(m.version)
  return opts
}

const anyLocked = computed(() => spec.mods.some((m) => m.version))

function unlockAll() {
  spec.mods = spec.mods.map((m) => ({ ...m, version: null }))
  modNotice.value = 'All mods now follow the latest Workshop version.'
}

// Add a single addon (#97): a Reforger server config lists only the top-level
// mod — its sub-dependencies are downloaded by the server automatically, so we
// never resolve, add or list them here. (Scenarios/terrains are different: they
// DO carry their dependency tree, and that path is left untouched on step 1.)
async function mergeModFromWorkshop(id) {
  const asset = await api(`/api/workshop/asset/${id}`)
  const resolved = {
    root: asset.id,
    mods: [{
      modId: asset.id,
      name: asset.name,
      versions: asset.versions || [],
      provides_scenarios: !!asset.scenarios?.length,
      dependencies: [], // top-level only — deps download automatically (#97)
    }],
  }
  spec.mods = mergeResolved(normalizeMods(spec.mods), resolved)
  return byModId(asset.id)
}

// Adding an id that is already on the list is harmless (the merge deduplicates)
// but silently saying "Added" hid what happened — say it out loud instead (#106).
function alreadyEnabledNotice(mods) {
  const names = mods.map((m) => m.name || m.modId).join(', ')
  return mods.length === 1
    ? `"${names}" is already enabled — nothing was added.`
    : `Already enabled, nothing added: ${names}.`
}

async function addModById(id) {
  if (isEnabled(id)) {
    modAdd.error = ''
    modNotice.value = alreadyEnabledNotice([byModId(id)])
    return
  }
  modAdd.busy = true
  modAdd.error = ''
  modNotice.value = ''
  try {
    const rootMod = await mergeModFromWorkshop(id)
    // Warn if the mod just added carries its own scenario (#69).
    if (rootMod && isExtraScenarioMod(rootMod)) {
      modNotice.value =
        `"${rootMod.name || rootMod.modId}" publishes its own scenario(s). A server runs only ` +
        `the scenario picked on step 1, so this is enabled for its content — to play its ` +
        `scenario, set it on step 1 instead.`
    }
  } catch (e) {
    modAdd.error = e.message
  } finally {
    modAdd.busy = false
  }
}

// Several ids pasted at once (#104): add what resolves, keep what didn't in the
// field so a typo doesn't cost the rest of the list.
async function addModsByIds(ids) {
  modAdd.busy = true
  modAdd.error = ''
  modNotice.value = ''
  const dupes = ids.filter(isEnabled).map(byModId)
  const added = []
  const failed = []
  for (const id of ids.filter((i) => !isEnabled(i))) {
    try {
      added.push(await mergeModFromWorkshop(id))
    } catch (e) {
      failed.push({ id, msg: e.message })
    }
  }
  modAdd.busy = false
  const notices = []
  if (added.length) {
    notices.push(`Added ${added.length} mod(s): ${added.map((m) => m.name || m.modId).join(', ')}.`)
  }
  if (dupes.length) notices.push(alreadyEnabledNotice(dupes))
  modNotice.value = notices.join(' ')
  if (failed.length) {
    modAdd.error = `Could not add: ${failed.map((f) => `${f.id} (${f.msg})`).join('; ')}`
    search.q = failed.map((f) => f.id).join(', ')
  } else {
    search.q = ''
  }
}

// The single Mods-step field: pasted ids / Workshop URLs add those mods
// directly — several at once, comma-separated (#104); anything else is a
// free-text Workshop search (#97).
async function submitModSearch() {
  const raw = search.q.trim()
  if (!raw) return
  const ids = extractModIds(raw)
  if (ids.length === 1) {
    // Single id keeps the richer one-mod flow (scenario-mod warning, #69)
    await addModById(ids[0])
    if (!modAdd.error) search.q = ''
    return
  }
  if (ids.length > 1) {
    await addModsByIds(ids)
    return
  }
  await runSearch()
}

function removeMod(id) {
  modNotice.value = ''
  const mod = byModId(id)
  if (!mod) return
  if (mod.from_scenario) {
    modNotice.value = `"${mod.name || id}" backs the selected scenario — change the scenario on step 1 to remove it.`
    return
  }
  // Still required by the scenario's mod → demote to a dependency, don't drop.
  if (stillRequiredWithoutExplicit(spec.mods, id)) {
    const reqs = requiredByNames(id)
    spec.mods = spec.mods.map((m) =>
      m.modId === id ? { ...m, explicit: false, from_scenario: false } : m,
    )
    modNotice.value = `Kept "${mod.name || id}" as a dependency — still required by ${reqs}.`
    return
  }
  spec.mods = spec.mods.filter((m) => m.modId !== id)
}

// Real sorts, not view toggles (#105): the list order is what config.json gets.
function sortByName() {
  spec.mods = sortModsByName(spec.mods)
  modNotice.value = 'Mods sorted by name.'
}

function sortByAdded() {
  spec.mods = sortModsByAdded(spec.mods)
  modNotice.value = 'Mods sorted in the order they were added.'
}

// Deterministic and offline: every mod after what it requires, everything else
// left where it is (#164).
function sortByDependencies() {
  const before = spec.mods
  spec.mods = topoOrder(spec.mods)
  const moved = movedMods(before, spec.mods).length
  modNotice.value = moved
    ? `Moved ${moved} mod(s) so each one is listed after what it requires.`
    : 'Every mod was already listed after what it requires — nothing moved.'
}

function moveOne(id, dir) {
  spec.mods = moveMod(spec.mods, id, dir)
}

// ---- Drag and drop (#164) ---------------------------------------------------
// Native HTML5 drag events — no new dependency for what is one splice. The
// ↑/↓ buttons stay: they are the keyboard and touch path, which drag is not.
const drag = reactive({ from: -1, over: -1 })

function onDragStart(i, event) {
  drag.from = i
  drag.over = i
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    // Firefox starts no drag at all unless some data is set.
    event.dataTransfer.setData('text/plain', String(i))
  }
}

function onDragOver(i, event) {
  if (drag.from < 0) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  drag.over = i
}

function onDrop(i) {
  if (drag.from >= 0 && drag.from !== i) spec.mods = reorderMods(spec.mods, drag.from, i)
  onDragEnd()
}

function onDragEnd() {
  drag.from = -1
  drag.over = -1
}

// ---- Order the mods with an AI (#164) ---------------------------------------
// Two routes to the same place, because there is no AI service that is both
// free and keyless to call from here (checked: the "anonymous" endpoints either
// want payment now or have retired their free tier):
//
//   * copy the prompt into ChatGPT / Gemini / Claude, paste the answer back —
//     free, needs no setup, and the mod list leaves through the user's own
//     browser rather than through their server;
//   * one click, when the operator has pointed AI_ORDER_URL at an OpenAI-
//     compatible service of their own (a free Gemini or OpenRouter key, or a
//     local Ollama).
//
// Both end at the same preview: an answer is never applied until it has been
// read back as a list of mod ids, checked against the mods actually in the
// template, and confirmed.
const aiOrder = reactive({
  open: false,
  loading: false, // fetching the prompt
  busy: false, // waiting on the AI service
  available: false, // a provider is configured on this manager
  prompt: '',
  reply: '',
  model: '',
  error: '',
  copied: false,
  fixDeps: true,
})
const aiPromptBox = ref(null)

function modsForPrompt() {
  return spec.mods.map((m) => ({
    modId: m.modId,
    name: m.name,
    explicit: m.explicit,
    dependencies: m.dependencies,
  }))
}

async function openAiOrder() {
  Object.assign(aiOrder, {
    open: true, loading: true, busy: false, prompt: '', reply: '', model: '',
    error: '', copied: false, fixDeps: true,
  })
  try {
    const res = await api('/api/mods/order/prompt', {
      method: 'POST',
      body: { mods: modsForPrompt() },
    })
    aiOrder.prompt = res.prompt
    aiOrder.available = res.ai_available
  } catch (e) {
    aiOrder.error = e.message
  } finally {
    aiOrder.loading = false
  }
}

async function askAi() {
  aiOrder.busy = true
  aiOrder.error = ''
  try {
    const res = await api('/api/mods/order/ai', {
      method: 'POST',
      body: { mods: modsForPrompt() },
    })
    aiOrder.reply = res.reply
    aiOrder.model = res.model
  } catch (e) {
    aiOrder.error = e.message
  } finally {
    aiOrder.busy = false
  }
}

// navigator.clipboard exists only in a secure context, which a plain-HTTP LAN
// deployment is not — so fall back to selecting the text, which works anywhere
// and leaves the user one Ctrl+C away.
async function copyPrompt() {
  aiOrder.copied = false
  try {
    await navigator.clipboard.writeText(aiOrder.prompt)
    aiOrder.copied = true
    setTimeout(() => (aiOrder.copied = false), 2500)
  } catch {
    aiPromptBox.value?.select()
    aiOrder.error = 'Could not reach the clipboard — the prompt is selected, press Ctrl+C to copy.'
  }
}

// What the reply would do, recomputed as it is typed or pasted. Everything the
// user needs to judge it is here: what moves, what the answer forgot, what it
// made up, and whether the result still lists each mod after what it requires.
const aiProposal = computed(() => {
  const ids = extractModIds(aiOrder.reply)
  if (!ids.length) return null
  const applied = applyOrderIds(spec.mods, ids)
  const proposed = aiOrder.fixDeps ? topoOrder(applied.mods) : applied.mods
  return {
    ...applied,
    mods: proposed,
    moved: movedMods(spec.mods, proposed),
    violations: dependencyViolations(proposed),
    repaired: aiOrder.fixDeps ? movedMods(applied.mods, proposed).length : 0,
  }
})

function applyAiOrder() {
  if (!aiProposal.value) return
  const moved = aiProposal.value.moved.length
  spec.mods = aiProposal.value.mods
  aiOrder.open = false
  modNotice.value = moved
    ? `Applied the suggested order — ${moved} mod(s) moved. Nothing was added or removed.`
    : 'That order matches the list you already have — nothing moved.'
}

// ---- Load a saved mod template into this list (#166) ------------------------
// A mod template is a named mod list kept on the Mod Templates page. Loading one
// only changes the list you are editing here — nothing is saved until you press
// Save, and the preview below the picker says exactly what would change first.
const modTemplateLoad = reactive({
  open: false,
  loading: false,
  applying: false,
  error: '',
  list: [],
  selectedId: '',
  mods: [],       // the selected mod template's mods, fetched on selection
  replace: false, // false = add to the list, true = replace it
})

async function openModTemplates() {
  modTemplateLoad.open = true
  modTemplateLoad.error = ''
  modTemplateLoad.selectedId = ''
  modTemplateLoad.mods = []
  modTemplateLoad.loading = true
  try {
    modTemplateLoad.list = await api('/api/mod-templates')
  } catch (e) {
    modTemplateLoad.error = e.message
  } finally {
    modTemplateLoad.loading = false
  }
}

async function selectModTemplate(id) {
  modTemplateLoad.selectedId = id
  modTemplateLoad.mods = []
  modTemplateLoad.error = ''
  if (!id) return
  modTemplateLoad.loading = true
  try {
    modTemplateLoad.mods = (await api(`/api/mod-templates/${id}`)).mods
  } catch (e) {
    modTemplateLoad.error = e.message
  } finally {
    modTemplateLoad.loading = false
  }
}

// What loading it would do, computed against the live list — so switching
// between "add" and "replace" updates the numbers before anything moves.
const modTemplatePreview = computed(() => {
  if (!modTemplateLoad.selectedId || !modTemplateLoad.mods.length) return null
  return applyModTemplate(spec.mods, modTemplateLoad.mods, {
    replace: modTemplateLoad.replace,
  })
})

function applyModTemplateToSpec() {
  const result = modTemplatePreview.value
  if (!result) return
  const chosen = modTemplateLoad.list.find((t) => t.id === modTemplateLoad.selectedId)
  spec.mods = result.mods
  modTemplateLoad.open = false
  const parts = [`Loaded "${chosen?.name}".`]
  if (result.added.length) parts.push(`${result.added.length} mod(s) added.`)
  if (result.removed.length) parts.push(`${result.removed.length} removed.`)
  if (result.relocked.length) parts.push(`${result.relocked.length} version lock(s) changed.`)
  if (parts.length === 1) parts.push('Every mod on it was already enabled — nothing changed.')
  modNotice.value = parts.join(' ')
  hydrateVersionHistories()
}

// ---- Export / import the enabled mod list as JSON (issue #55) ---------------
function exportMods() {
  const payload = { format: MODS_FILE_FORMAT, mods: orderedMods(spec.mods) }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = (spec.name || 'mods').replace(/[^a-z0-9_-]/gi, '_') + '-mods.json'
  a.click()
  URL.revokeObjectURL(a.href)
}

async function importMods(event) {
  const file = event.target.files?.[0]
  if (!file) return
  modAdd.error = ''
  modNotice.value = ''
  try {
    const parsed = JSON.parse(await file.text())
    const list = Array.isArray(parsed) ? parsed : parsed.mods
    if (!Array.isArray(list)) throw new Error('No "mods" array in that file.')
    if (
      spec.mods.length &&
      !confirm(`Replace the current ${spec.mods.length} mod(s) with ${list.length} from this file?`)
    ) {
      return
    }
    // A file written before #164 carries no meaningful order of its own — it was
    // always picks-then-dependencies — so it is re-partitioned. A @2 file was
    // saved from a list the user ordered themselves: take it exactly as it is.
    const loaded = normalizeMods(list)
    spec.mods = parsed.format === MODS_FILE_FORMAT ? loaded : partitionOrder(loaded)
    modNotice.value = `Loaded ${spec.mods.length} mod(s) from ${file.name}.`
    hydrateVersionHistories()
  } catch (e) {
    modAdd.error = `Could not import mods: ${e.message}`
  } finally {
    if (modsFileInput.value) modsFileInput.value.value = ''
  }
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
    // The template name isn't part of config.json, but the spec model requires
    // one — so a new template previewed before step 4 would render an error
    // instead of a config, and "Edit JSON" would open on that error text. Send a
    // placeholder: it changes nothing in the output (#29).
    const body = { ...spec, name: spec.name || 'Untitled' }
    const cfg = await api('/api/templates/preview', { method: 'POST', body })
    preview.value = JSON.stringify(cfg, null, 2)
  } catch (e) {
    preview.value = `// ${e.message}`
  }
}

watch(
  () => JSON.stringify(spec),
  () => {
    if (rawMode.value) return // don't re-render under the user's cursor mid-edit
    clearTimeout(previewTimer)
    previewTimer = setTimeout(refreshPreview, 250)
  },
)

// ---- Edit the config.json by hand (issue #29) -------------------------------
// The preview panel doubles as the editor. Known keys typed here flow back into
// the wizard's fields; anything the wizard doesn't model is kept as `extras` and
// re-applied on every future save, so custom (often scenario-specific)
// gameProperties survive.
const rawMode = ref(false)
const draft = ref('')
const rawErrors = ref([])
const rawWarnings = ref([])
const rawSyntaxError = ref('')
const applying = ref(false)
let validateTimer = null

// The custom-key paths the backend found, for the panel badge. Server-derived on
// purpose: "what counts as a custom key" is the validator's call, so the badge
// and the editor's warnings always report the same number.
const extrasPaths = ref([])
const extrasCount = computed(() => extrasPaths.value.length)

function startRawEdit() {
  draft.value = preview.value
  rawErrors.value = []
  rawWarnings.value = []
  rawSyntaxError.value = ''
  rawMode.value = true
  validateDraft()
}

function cancelRawEdit() {
  rawMode.value = false
  clearTimeout(validateTimer)
  refreshPreview()
}

function parseDraft() {
  try {
    const parsed = JSON.parse(draft.value)
    rawSyntaxError.value = ''
    return parsed
  } catch (e) {
    // CodeMirror already marks the spot; this line says what's wrong in words.
    rawSyntaxError.value = e.message
    return null
  }
}

async function validateDraft() {
  const parsed = parseDraft()
  if (parsed === null) {
    rawErrors.value = []
    rawWarnings.value = []
    return
  }
  try {
    const res = await api('/api/templates/validate', { method: 'POST', body: parsed })
    rawErrors.value = res.errors
    rawWarnings.value = res.warnings
  } catch (e) {
    rawErrors.value = [{ path: '', message: e.message }]
  }
}

watch(draft, () => {
  clearTimeout(validateTimer)
  validateTimer = setTimeout(validateDraft, 400)
})

const canApply = computed(
  () => !applying.value && !rawSyntaxError.value && rawErrors.value.length === 0,
)

async function applyRawEdit() {
  const parsed = parseDraft()
  if (parsed === null) return
  applying.value = true
  error.value = ''
  try {
    const res = await api('/api/templates/reconcile', {
      method: 'POST',
      body: { spec, config: parsed },
    })
    Object.assign(spec, res.spec)
    extrasPaths.value = res.warnings.map((w) => w.path)
    rawMode.value = false
    refreshPreview()
  } catch (e) {
    rawErrors.value = [{ path: '', message: e.message }]
  } finally {
    applying.value = false
  }
}

// ---- Import an existing config.json (issue #35) ----------------------------
const importing = ref(false)
const importInput = ref(null)

async function importConfigFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  importing.value = true
  error.value = ''
  try {
    const text = await file.text()
    let parsed
    try {
      parsed = JSON.parse(text)
    } catch {
      throw new Error('That file is not valid JSON.')
    }
    const { spec: imported } = await api('/api/templates/import', {
      method: 'POST',
      body: parsed,
    })
    const launchDefaults = { ...spec.launch }
    Object.assign(spec, imported)
    // config.json carries no launch args, so keep the defaults for those
    spec.launch = { ...launchDefaults, ...(imported.launch || {}) }
    // config.json has only a flat mods[]; treat each as an explicit pick (#55)
    spec.mods = normalizeMods(imported.mods)
    hydrateVersionHistories()
    // config.json has no template name; suggest one from the in-game name/file
    if (!spec.name) spec.name = imported.game_name || file.name.replace(/\.json$/i, '')
    if (spec.scenario_id) {
      step.value = 3 // jump to Settings so the imported values can be reviewed
    }
    // else stay on step 1 so the user picks a scenario (config had none)
  } catch (e) {
    error.value = e.message
  } finally {
    importing.value = false
    if (importInput.value) importInput.value.value = '' // allow re-importing
  }
}

// ---- Save ------------------------------------------------------------------
const savedFlash = ref(false)
let savedFlashTimer = null

async function persistTemplate() {
  if (editing.value) {
    await api(`/api/templates/${props.id}`, { method: 'PUT', body: spec })
  } else {
    await api('/api/templates', { method: 'POST', body: spec })
  }
}

async function save() {
  error.value = ''
  saving.value = true
  try {
    await persistTemplate()
    router.push({ name: 'templates' })
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

// The header's save button (#107): saves where you are and stays, so a mod or
// setting change doesn't need a trip to step 4. Only offered while editing —
// a new template can't be saved before it's named, and that happens on step 4,
// where the classic save-and-return button already is.
async function saveAndStay() {
  error.value = ''
  saving.value = true
  try {
    await persistTemplate()
    savedFlash.value = true
    clearTimeout(savedFlashTimer)
    savedFlashTimer = setTimeout(() => (savedFlash.value = false), 2500)
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
    await heartbeatLock()
    lockTimer = setInterval(heartbeatLock, 30000)
    window.addEventListener('pagehide', releaseLock)
    try {
      const t = await api(`/api/templates/${props.id}`)
      const launchDefaults = { ...spec.launch }
      Object.assign(spec, t.spec)
      // merge launch onto defaults so older templates (empty launch) keep keys
      spec.launch = { ...launchDefaults, ...(t.spec.launch || {}) }
      // normalise mods so older templates (flat mods[]) gain the metadata
      // fields, and put them in the order this template has been rendering with
      // — before #164 that was always "picks first, dependencies after",
      // whatever order the array itself was stored in (#164).
      spec.mods = partitionOrder(normalizeMods(t.spec.mods))
      spec.name = t.name
      spec.description = t.description
      extrasPaths.value = t.extras_paths || []
      hydrateVersionHistories() // deliberately not awaited — fills pickers as results land
    } catch (e) {
      error.value = e.message
    }
  }
  refreshPreview()
})

onBeforeUnmount(() => {
  clearTimeout(savedFlashTimer)
  clearInterval(lockTimer)
  window.removeEventListener('pagehide', releaseLock)
  releaseLock()
})
</script>

<template>
  <div class="container">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">{{ editing ? 'Edit template' : 'New template' }}</h1>
      <div class="d-flex align-items-center gap-2">
        <span v-if="savedFlash" class="text-success small">✓ Saved</span>
        <button
          v-if="editing && step > 1"
          class="btn btn-primary btn-sm"
          :disabled="saving || locked"
          title="Save the template and keep editing"
          @click="saveAndStay"
        >
          {{ saving ? 'Saving…' : 'Save changes' }}
        </button>
        <button class="btn btn-outline-secondary btn-sm" @click="router.push({ name: 'templates' })">
          Cancel
        </button>
      </div>
    </div>

    <div v-if="locked" class="alert alert-warning py-2">
      🔒 This template is being edited in another session, so Save is disabled here.
      You can still look around — this page unlocks by itself when the other editor leaves.
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
          <!-- Import an existing config.json to pre-fill the wizard (issue #35) -->
          <div v-if="!editing" class="card bg-body-tertiary mb-3">
            <div class="card-body py-2">
              <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                <div>
                  <div class="fw-semibold small">Start from an existing config.json</div>
                  <div class="text-secondary small">
                    Upload a Reforger server config (or a template exported from here) to
                    auto-fill the scenario, mods and settings.
                  </div>
                </div>
                <label class="btn btn-outline-primary btn-sm mb-0">
                  <span v-if="importing" class="spinner-border spinner-border-sm me-1"></span>
                  {{ importing ? 'Importing…' : 'Upload config.json' }}
                  <input
                    ref="importInput"
                    type="file"
                    accept=".json,application/json"
                    class="d-none"
                    :disabled="importing"
                    @change="importConfigFile"
                  />
                </label>
              </div>
            </div>
          </div>

          <!-- Current scenario (#59): what's picked now, with a way to clear it -->
          <div v-if="spec.scenario_id" class="card border-info-subtle mb-3">
            <div class="card-body py-2 d-flex flex-wrap justify-content-between align-items-center gap-2">
              <div class="me-2" style="min-width: 0">
                <div class="text-secondary small text-uppercase" style="letter-spacing: .04em">
                  Current scenario
                </div>
                <div class="fw-semibold">
                  {{ scenarioDisplayName }}
                  <span v-if="spec.scenario_player_count" class="badge text-bg-secondary fw-normal ms-1">
                    {{ spec.scenario_player_count }} players
                  </span>
                </div>
                <small class="text-secondary d-block text-break">{{ spec.scenario_id }}</small>
                <small v-if="scenarioSourceMod" class="text-secondary">
                  from Workshop mod "{{ scenarioSourceMod.name || scenarioSourceMod.modId }}"
                </small>
              </div>
              <button
                class="btn btn-outline-danger btn-sm flex-shrink-0"
                title="Clear the scenario (and the mods it brought in)"
                @click="removeScenarioPrompt = true"
              >Remove scenario</button>
            </div>
          </div>

          <p class="text-secondary">
            {{ spec.scenario_id
              ? 'To replace the scenario, search the Workshop and pick a new one — you\'ll be asked to confirm.'
              : 'Search the Workshop and pick a scenario. Its mod and all dependencies are added automatically.' }}
          </p>
          <p class="text-secondary small">
            Search <strong>scenarios</strong> or <strong>terrains</strong> — a terrain (map)
            usually ships playable scenarios of its own, and often more than one. Pick the
            asset, then choose which of its scenarios this server runs.
          </p>
          <div class="input-group mb-3">
            <input
              v-model="search.q"
              class="form-control"
              placeholder="Search scenarios or terrains, e.g. Conflict, Overthrow, Visingsö…"
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
              :class="{ active: scenarioPick.asset?.asset?.id === row.id }"
              @click="pickScenarioAsset(row)"
            >
              <div class="d-flex justify-content-between">
                <span class="fw-semibold">
                  {{ row.name }}
                  <span class="badge fw-normal ms-1" :class="assetKindBadge[row.kind] || 'text-bg-secondary'">
                    {{ row.kind }}
                  </span>
                </span>
                <small class="text-secondary">{{ fmtSize(row.size) }}</small>
              </div>
              <small class="text-secondary">by {{ row.author }} · v{{ row.version }}</small>
            </button>
          </div>

          <div v-if="scenarioPick.busy" class="text-secondary">Loading scenarios…</div>
          <div v-if="scenarioPick.error" class="alert alert-info py-2 small">
            {{ scenarioPick.error }}
          </div>
          <div v-if="scenarioPick.asset" class="card border-primary-subtle">
            <div class="card-body">
              <h2 class="h6 mb-1">
                {{ scenarioPick.asset.asset.name }}
                <span class="badge fw-normal" :class="assetKindBadge[pickedAssetKind] || 'text-bg-secondary'">
                  {{ pickedAssetKind }}
                </span>
              </h2>
              <!-- A server runs exactly one scenario, so a multi-scenario asset has to
                   be asked about rather than guessed at (#79 follow-up). -->
              <p v-if="pickedScenarios.length > 1" class="small mb-3">
                This {{ pickedAssetKind }} provides
                <strong>{{ pickedScenarios.length }} scenarios</strong> — choose the one this
                server should run. (A server runs one scenario; save another template for the
                others.)
              </p>
              <p v-else class="text-secondary small mb-3">
                One scenario — select it to continue.
              </p>
              <div class="list-group">
                <button
                  v-for="sc in pickedScenarios"
                  :key="sc.scenario_id"
                  class="list-group-item list-group-item-action"
                  @click="chooseScenario(sc, scenarioPick.asset)"
                >
                  <div class="fw-semibold">{{ sc.name }}</div>
                  <small class="text-secondary d-block text-break">{{ sc.scenario_id }}</small>
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
            Add mods on top of the scenario. Only the mod itself goes in the config — its
            sub-dependencies are downloaded by the server automatically, so you don't add or
            remove them here, though you can move them in the list. Mods follow the latest
            Workshop release unless you lock a version — only locked versions are written to
            config.json.
          </p>
          <p class="text-secondary small">
            <span class="badge text-bg-info">scenario</span> provides the selected scenario ·
            <span class="badge text-bg-secondary">scenario dependency</span> needed for the
            scenario to work ·
            <span class="badge text-bg-primary">addon</span> extra mod you chose ·
            <span class="badge text-bg-warning">scenario mod</span> an addon that carries its
            own (unused) scenario
          </p>

          <!-- A second scenario slipped in as a mod (#69): a server runs one. -->
          <div v-if="extraScenarioMods.length" class="alert alert-warning py-2 small">
            <template v-if="extraScenarioMods.length === 1">
              <strong>{{ extraScenarioMods[0].name || extraScenarioMods[0].modId }}</strong>
              publishes its own scenario. A Reforger server runs only the one scenario picked
              on step 1, so it stays enabled for its content, not as a second playable
              scenario. To play it instead, set it as the scenario on step 1.
            </template>
            <template v-else>
              <strong>{{ extraScenarioMods.map((m) => m.name || m.modId).join(', ') }}</strong>
              publish their own scenarios. A Reforger server runs only the one scenario picked
              on step 1, so they stay enabled for their content, not as extra playable
              scenarios. To play one instead, set it as the scenario on step 1.
            </template>
          </div>

          <!-- Add mods: one field takes free text OR a mod id / Workshop URL (#97) -->
          <div class="input-group mb-2">
            <input
              v-model="search.q"
              class="form-control"
              placeholder="Search the Workshop, or paste mod ids / Workshop URLs (comma-separated)…"
              @keyup.enter="submitModSearch"
            />
            <button
              class="btn btn-primary"
              :disabled="search.busy || modAdd.busy || !search.q.trim()"
              @click="submitModSearch"
            >
              {{
                search.busy || modAdd.busy
                  ? 'Working…'
                  : searchModIds.length > 1
                    ? `Add ${searchModIds.length} mods`
                    : searchIsModId
                      ? 'Add'
                      : 'Search'
              }}
            </button>
          </div>
          <div v-if="search.error" class="alert alert-warning py-2 small mb-2">{{ search.error }}</div>
          <div
            v-if="search.results.length"
            class="list-group mb-2"
            style="max-height: 15rem; overflow-y: auto"
          >
            <div
              v-for="row in search.results"
              :key="row.id"
              class="list-group-item d-flex justify-content-between align-items-center py-2"
            >
              <div class="me-2 text-truncate">
                <div class="fw-semibold text-truncate">{{ row.name }}</div>
                <small class="text-secondary">v{{ row.version }} · {{ fmtSize(row.size) }}</small>
              </div>
              <button
                class="btn btn-sm btn-outline-primary flex-shrink-0"
                :disabled="modAdd.busy || isEnabled(row.id)"
                @click="addModById(row.id)"
              >{{ isEnabled(row.id) ? 'Added' : 'Add' }}</button>
            </div>
          </div>
          <div v-if="modAdd.busy" class="text-secondary small mb-2">
            <span class="spinner-border spinner-border-sm me-1"></span>Adding mod…
          </div>
          <div v-if="modAdd.error" class="alert alert-info py-2 small mb-2">{{ modAdd.error }}</div>
          <div v-if="modNotice" class="alert alert-secondary py-2 small mb-2">{{ modNotice }}</div>

          <!-- Enabled mods overview -->
          <div class="d-flex justify-content-between align-items-center mt-3 mb-2">
            <h2 class="h6 mb-0">
              Enabled mods ({{ spec.mods.length }})
              <small v-if="hydratingVersions" class="text-secondary fw-normal ms-1">
                <span class="spinner-border spinner-border-sm me-1" style="width: .75rem; height: .75rem"></span>
                fetching version history…
              </small>
            </h2>
            <div class="btn-group btn-group-sm">
              <button
                class="btn btn-outline-secondary"
                title="Load a saved mod template — a named mod list from the Mod Templates page"
                @click="openModTemplates"
              >🧰 Mod template…</button>
              <button
                class="btn btn-outline-secondary"
                :disabled="spec.mods.length < 2"
                title="Order the mods with an AI — copy the prompt into ChatGPT, Gemini or Claude, or ask a service you have configured"
                @click="openAiOrder"
              >✨ AI order…</button>
              <button
                class="btn btn-outline-secondary"
                :disabled="spec.mods.length < 2"
                title="Reorder so every mod is listed after the mods it requires, leaving everything else where it is"
                @click="sortByDependencies"
              >Dependencies first</button>
              <button
                class="btn btn-outline-secondary"
                :disabled="spec.mods.length < 2"
                title="Sort the mods alphabetically — this is the order saved to config.json"
                @click="sortByName"
              >Sort A–Z</button>
              <button
                class="btn btn-outline-secondary"
                :disabled="spec.mods.length < 2"
                title="Sort the mods in the order they were added"
                @click="sortByAdded"
              >Sort as added</button>
              <button
                class="btn btn-outline-secondary"
                :disabled="!anyLocked"
                title="Clear every version lock so all mods follow the latest Workshop release"
                @click="unlockAll"
              >Unlock all</button>
              <button
                class="btn btn-outline-secondary"
                :disabled="!spec.mods.length"
                title="Save the enabled mod list to a JSON file"
                @click="exportMods"
              >Export JSON</button>
              <label class="btn btn-outline-secondary mb-0" title="Load an enabled mod list from a JSON file">
                Import JSON
                <input
                  ref="modsFileInput"
                  type="file"
                  accept=".json,application/json"
                  class="d-none"
                  @change="importMods"
                />
              </label>
            </div>
          </div>

          <div v-if="!spec.mods.length" class="text-secondary small">
            No mods yet — the scenario runs vanilla. Add mods above.
          </div>

          <!-- One list, in load order (#164): drag a row anywhere, dependencies
               included. Row 1 is the first entry of config.json's mods[]. -->
          <div v-if="spec.mods.length" class="text-secondary small mb-1">
            This is the order the mods are written to <code>config.json</code>. Drag a row
            (or use ↑ ↓) to change it.
            <span v-if="depOrderWarnings.length" class="text-warning-emphasis">
              {{ depOrderWarnings.length }} mod(s) are listed before something they require —
              "Dependencies first" fixes that.
            </span>
          </div>
          <ul v-if="spec.mods.length" class="list-group mb-2">
            <li
              v-for="(m, i) in spec.mods"
              :key="m.modId"
              class="list-group-item d-flex justify-content-between align-items-center py-2"
              :class="{
                'bg-body-tertiary': !m.explicit,
                'opacity-50': drag.from === i,
                'border-primary border-2': drag.over === i && drag.from !== i && drag.from >= 0,
              }"
              draggable="true"
              @dragstart="onDragStart(i, $event)"
              @dragover="onDragOver(i, $event)"
              @drop="onDrop(i)"
              @dragend="onDragEnd"
            >
              <span
                class="text-secondary me-2 flex-shrink-0 text-nowrap"
                style="cursor: grab"
                title="Drag to reorder"
              >
                <span class="font-monospace small">{{ i + 1 }}</span> ⠿
              </span>
              <div class="me-2 text-truncate">
                <span
                  class="badge me-1"
                  :class="modBadge(m).cls"
                  :title="modBadge(m).title"
                >{{ modBadge(m).text }}</span>
                <span :class="m.explicit ? 'fw-semibold' : ''">{{ m.name || m.modId }}</span>
                <small
                  v-if="!m.explicit"
                  class="text-secondary ms-1"
                  :title="'Required by: ' + requiredByNames(m.modId)"
                >· required by {{ requiredBy(spec.mods, m.modId).length }}</small>
                <small class="text-secondary d-block">{{ m.modId }}</small>
              </div>
              <select
                v-model="m.version"
                class="form-select form-select-sm w-auto ms-auto me-2 flex-shrink-0"
                :title="m.version ? 'Locked to v' + m.version : 'Follows the latest Workshop release'"
              >
                <option :value="null">latest</option>
                <option v-for="v in lockOptions(m)" :key="v" :value="v">🔒 v{{ v }}</option>
              </select>
              <div class="btn-group btn-group-sm flex-shrink-0">
                <button
                  class="btn btn-outline-secondary"
                  :disabled="i === 0"
                  title="Move up"
                  @click="moveOne(m.modId, -1)"
                >↑</button>
                <button
                  class="btn btn-outline-secondary"
                  :disabled="i === spec.mods.length - 1"
                  title="Move down"
                  @click="moveOne(m.modId, 1)"
                >↓</button>
                <button
                  v-if="m.explicit"
                  class="btn btn-outline-danger"
                  :title="m.from_scenario ? 'Change the scenario to remove this' : 'Remove'"
                  @click="removeMod(m.modId)"
                >✕</button>
                <button
                  v-else
                  class="btn btn-outline-secondary"
                  disabled
                  title="Added automatically because another mod requires it — remove that mod to drop this one"
                >✕</button>
              </div>
            </li>
          </ul>
        </div>

        <!-- Load a saved mod template (#166) -->
        <div
          v-if="modTemplateLoad.open"
          class="modal d-block"
          tabindex="-1"
          style="background: rgba(0,0,0,.5)"
        >
          <div class="modal-dialog modal-lg modal-dialog-scrollable">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title">Load a mod template</h5>
                <button class="btn-close" @click="modTemplateLoad.open = false"></button>
              </div>
              <div class="modal-body">
                <p class="small text-secondary">
                  A mod template is a saved mod list, in the order you arranged it. Loading
                  one changes only the mods of this server template, and only after you press
                  <em>Load</em> — and it is still not saved until you save the template.
                </p>

                <div v-if="modTemplateLoad.error" class="alert alert-warning py-2 small">
                  {{ modTemplateLoad.error }}
                </div>
                <div v-if="modTemplateLoad.loading" class="text-secondary small">
                  <span class="spinner-border spinner-border-sm me-1"></span>Loading…
                </div>

                <div
                  v-else-if="!modTemplateLoad.list.length"
                  class="text-secondary small"
                >
                  No mod templates yet. Create one on the
                  <router-link :to="{ name: 'mod-templates' }">Mod Templates</router-link>
                  page — it is a mod list you can reuse in every server template.
                </div>

                <template v-else>
                  <div class="list-group mb-3">
                    <label
                      v-for="mt in modTemplateLoad.list"
                      :key="mt.id"
                      class="list-group-item d-flex align-items-center gap-2"
                    >
                      <input
                        class="form-check-input mt-0 flex-shrink-0"
                        type="radio"
                        :value="mt.id"
                        :checked="modTemplateLoad.selectedId === mt.id"
                        @change="selectModTemplate(mt.id)"
                      />
                      <span class="text-truncate">
                        <span class="fw-semibold">{{ mt.name }}</span>
                        <span class="badge text-bg-secondary ms-1">{{ mt.mod_count }} mods</span>
                        <small class="text-secondary d-block">{{ mt.description }}</small>
                      </span>
                    </label>
                  </div>

                  <div class="form-check small">
                    <input
                      id="mt-add"
                      v-model="modTemplateLoad.replace"
                      class="form-check-input"
                      type="radio"
                      :value="false"
                    />
                    <label class="form-check-label" for="mt-add">
                      <strong>Add to this template</strong> — keep the mods that are already
                      here, append the rest in the mod template's order
                    </label>
                  </div>
                  <div class="form-check small mb-3">
                    <input
                      id="mt-replace"
                      v-model="modTemplateLoad.replace"
                      class="form-check-input"
                      type="radio"
                      :value="true"
                    />
                    <label class="form-check-label" for="mt-replace">
                      <strong>Replace the mod list</strong> — this template ends up with
                      exactly this mod list, in its order (the scenario's own mods stay)
                    </label>
                  </div>

                  <template v-if="modTemplatePreview">
                    <hr />
                    <h6 class="mb-2">What this would do</h6>
                    <ul class="small mb-2">
                      <li>
                        <strong>{{ modTemplatePreview.added.length }}</strong> mod(s) added.
                      </li>
                      <li v-if="modTemplatePreview.removed.length" class="text-warning-emphasis">
                        <strong>{{ modTemplatePreview.removed.length }}</strong> mod(s) removed
                        from this template.
                      </li>
                      <li v-if="modTemplatePreview.relocked.length">
                        {{ modTemplatePreview.relocked.length }} mod(s) take the mod template's
                        version lock.
                      </li>
                    </ul>
                    <ol class="list-group list-group-numbered" style="max-height: 18rem; overflow-y: auto">
                      <li
                        v-for="row in modTemplatePreview.mods"
                        :key="row.modId"
                        class="list-group-item py-1 small d-flex justify-content-between"
                        :class="{ 'list-group-item-warning': modTemplatePreview.added.includes(row.modId) }"
                      >
                        <span class="text-truncate">{{ row.name || row.modId }}</span>
                        <small class="text-secondary flex-shrink-0 ms-2">{{ row.modId }}</small>
                      </li>
                    </ol>
                  </template>
                </template>
              </div>
              <div class="modal-footer">
                <button class="btn btn-outline-secondary" @click="modTemplateLoad.open = false">
                  Cancel
                </button>
                <button
                  class="btn btn-primary"
                  :disabled="!modTemplatePreview"
                  @click="applyModTemplateToSpec"
                >Load these mods</button>
              </div>
            </div>
          </div>
        </div>

        <!-- Order the mods with an AI (#164) -->
        <div v-if="aiOrder.open" class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
          <div class="modal-dialog modal-lg modal-dialog-scrollable">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title">Order {{ spec.mods.length }} mods with an AI</h5>
                <button class="btn-close" @click="aiOrder.open = false"></button>
              </div>
              <div class="modal-body">
                <p class="small text-secondary">
                  The prompt below describes your mods — their ids, names and what requires
                  what — and asks for a load order back. Nothing about your server, its
                  password or its players is in it, and nothing moves until you press
                  <em>Apply</em>.
                </p>

                <div v-if="aiOrder.loading" class="text-secondary small">
                  <span class="spinner-border spinner-border-sm me-1"></span>Building the prompt…
                </div>

                <template v-else>
                  <!-- Route 1: a service the operator configured -->
                  <div v-if="aiOrder.available" class="mb-3">
                    <button class="btn btn-primary btn-sm" :disabled="aiOrder.busy" @click="askAi">
                      <span v-if="aiOrder.busy" class="spinner-border spinner-border-sm me-1"></span>
                      {{ aiOrder.busy ? 'Asking…' : 'Ask the AI service' }}
                    </button>
                    <span v-if="aiOrder.model" class="small text-secondary ms-2">
                      answered by {{ aiOrder.model }}
                    </span>
                  </div>

                  <!-- Route 2: the free web assistants, via the clipboard -->
                  <div class="mb-2">
                    <button class="btn btn-outline-secondary btn-sm me-2" @click="copyPrompt">
                      {{ aiOrder.copied ? 'Copied ✓' : 'Copy the prompt' }}
                    </button>
                    <span class="small text-secondary">then paste it into</span>
                    <a class="small ms-2" href="https://chatgpt.com/" target="_blank" rel="noopener noreferrer">ChatGPT</a>
                    <a class="small ms-2" href="https://gemini.google.com/" target="_blank" rel="noopener noreferrer">Gemini</a>
                    <a class="small ms-2" href="https://claude.ai/" target="_blank" rel="noopener noreferrer">Claude</a>
                    <span class="small text-secondary ms-2">— all free — and paste the answer below.</span>
                  </div>
                  <details class="mb-3">
                    <summary class="small text-secondary">Show the prompt</summary>
                    <textarea
                      ref="aiPromptBox"
                      class="form-control form-control-sm font-monospace mt-2"
                      rows="10"
                      readonly
                      :value="aiOrder.prompt"
                    ></textarea>
                  </details>

                  <label class="form-label small fw-semibold">Paste the answer</label>
                  <textarea
                    v-model="aiOrder.reply"
                    class="form-control form-control-sm font-monospace"
                    rows="6"
                    placeholder="Paste the AI's reply here — any format, as long as the mod ids are in it."
                  ></textarea>

                  <div v-if="aiOrder.error" class="alert alert-warning py-2 small mt-2 mb-0">
                    {{ aiOrder.error }}
                  </div>

                  <div v-if="aiOrder.reply.trim() && !aiProposal" class="alert alert-warning py-2 small mt-2 mb-0">
                    No mod ids in that text. Paste the whole reply — each id is 16 characters,
                    like <code>{{ spec.mods[0]?.modId }}</code>.
                  </div>

                  <template v-if="aiProposal">
                    <hr />
                    <h6 class="mb-2">What this would do</h6>
                    <ul class="small mb-2">
                      <li>
                        <strong>{{ aiProposal.moved.length }}</strong> of
                        {{ spec.mods.length }} mods change position. Nothing is added or removed.
                      </li>
                      <li v-if="aiProposal.missing.length" class="text-warning-emphasis">
                        The answer left out {{ aiProposal.missing.length }} mod(s) — they stay
                        where they are now, so the list is still complete.
                      </li>
                      <li v-if="aiProposal.unknown.length" class="text-warning-emphasis">
                        It named {{ aiProposal.unknown.length }} id(s) that are not in this
                        template. Ignored — nothing is added by this.
                      </li>
                      <li v-if="aiProposal.repaired" class="text-secondary">
                        {{ aiProposal.repaired }} mod(s) were nudged so each one is still listed
                        after what it requires.
                      </li>
                      <li v-if="aiProposal.violations.length" class="text-warning-emphasis">
                        {{ aiProposal.violations.length }} mod(s) would sit before something they
                        require (a dependency loop — it cannot be fixed automatically).
                      </li>
                    </ul>
                    <div class="form-check small mb-2">
                      <input
                        id="ai-fix-deps"
                        v-model="aiOrder.fixDeps"
                        class="form-check-input"
                        type="checkbox"
                      />
                      <label class="form-check-label" for="ai-fix-deps">
                        Keep every mod after the mods it requires, even if the answer says otherwise
                      </label>
                    </div>
                    <ol class="list-group list-group-numbered" style="max-height: 18rem; overflow-y: auto">
                      <li
                        v-for="row in aiProposal.mods"
                        :key="row.modId"
                        class="list-group-item py-1 small d-flex justify-content-between"
                        :class="{ 'list-group-item-warning': aiProposal.moved.some((r) => r.mod.modId === row.modId) }"
                      >
                        <span class="text-truncate">{{ row.name || row.modId }}</span>
                        <small class="text-secondary flex-shrink-0 ms-2">{{ row.modId }}</small>
                      </li>
                    </ol>
                  </template>
                </template>
              </div>
              <div class="modal-footer">
                <button class="btn btn-outline-secondary" @click="aiOrder.open = false">Cancel</button>
                <button class="btn btn-primary" :disabled="!aiProposal" @click="applyAiOrder">
                  Apply this order
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- STEP 3: SETTINGS -->
        <div v-show="step === 3">
          <!-- Say it out loud when picking a scenario moved a number the user
               may have set themselves (#65) — never change it silently. -->
          <div v-if="maxPlayersNotice" class="alert alert-info py-2 small">
            Max players was set to <strong>{{ maxPlayersNotice }}</strong> to match
            "{{ scenarioDisplayName }}". Override it below if you want.
          </div>
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
              <!-- Seeded from the scenario's Workshop player count, always overridable (#65) -->
              <small v-if="spec.scenario_player_count" class="d-block mt-1">
                <span v-if="maxPlayersOverridden" class="text-warning-emphasis">
                  "{{ scenarioDisplayName }}" is built for
                  {{ spec.scenario_player_count }} players.
                  <a href="#" class="ms-1" @click.prevent="resetMaxPlayers">Use {{ spec.scenario_player_count }}</a>
                </span>
                <span v-else class="text-secondary">
                  Matches the {{ spec.scenario_player_count }} players
                  "{{ scenarioDisplayName }}" declares. Change it if you want.
                </span>
              </small>
              <small v-else class="d-block mt-1 text-secondary">
                This scenario declares no player count — pick one yourself.
              </small>
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
              <label class="form-label">Min grass distance <small class="text-secondary">(≥ 50)</small></label>
              <input v-model.number="spec.server_min_grass_distance" type="number" min="50" max="150" class="form-control" />
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
              <div class="form-check mb-2">
                <input id="persist" v-model="spec.persistence_enabled" class="form-check-input" type="checkbox" />
                <label for="persist" class="form-check-label">Configure persistence</label>
              </div>
              <p class="text-secondary small mb-2">
                The server saves the mission on its own by default. Tick the box to write a
                <code>persistence</code> block and set these values yourself; leave it clear to let
                the engine decide. Unticking does not switch saving off — to stop a scenario saving
                at all, add <code>"missionHeader": &#123; "m_eSaveTypes": 0 &#125;</code> under
                <code>game.gameProperties</code> with Edit JSON.
              </p>
              <!-- Three equal columns, top-aligned. Do NOT add align-items-end
                   here: the help texts are different heights, so bottom-aligning
                   the columns lifts the taller one's label and input off the row. -->
              <div class="row g-2">
                <div class="col-6 col-md-4">
                  <label class="form-label small">Auto-save interval (min)</label>
                  <input v-model.number="spec.auto_save_interval" type="number" min="0" max="60"
                    class="form-control" :disabled="!spec.persistence_enabled" />
                  <div class="form-text">0 turns automatic saves off.</div>
                </div>
                <div class="col-6 col-md-4">
                  <label class="form-label small">Save points kept</label>
                  <input v-model.number="spec.save_retention" type="number" min="1" max="128"
                    class="form-control" :disabled="!spec.persistence_enabled" />
                  <div class="form-text">1–128 per mission.</div>
                </div>
                <div class="col-6 col-md-4">
                  <label class="form-label small">Hive ID</label>
                  <input v-model.number="spec.hive_id" type="number" min="0" max="16383"
                    class="form-control" :disabled="!spec.persistence_enabled" />
                  <div class="form-text">Separates saves when servers share a database.</div>
                </div>
              </div>
              <div class="d-flex gap-4 flex-wrap mt-2">
                <div class="form-check">
                  <input id="persist-load" v-model="spec.load_session_save" class="form-check-input"
                    type="checkbox" :disabled="!spec.persistence_enabled" />
                  <label for="persist-load" class="form-check-label small">
                    Load the latest save on startup
                  </label>
                </div>
                <div class="form-check">
                  <input id="persist-keep" v-model="spec.keep_session_save" class="form-check-input"
                    type="checkbox" :disabled="!spec.persistence_enabled" />
                  <label for="persist-keep" class="form-check-label small">
                    Keep save points after the mission ends
                  </label>
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

          <button class="btn btn-link px-0 mt-3" @click="showLaunch = !showLaunch">
            {{ showLaunch ? '▾ Hide' : '▸ Show' }} engine launch parameters
          </button>
          <div v-show="showLaunch" class="border-top pt-3">
            <p class="text-secondary small">
              Command-line parameters passed to the server engine (blank = engine default).
            </p>
            <div class="row g-3">
              <div v-for="[key, label] in launchNumFields" :key="key" class="col-6 col-md-3">
                <label class="form-label small">{{ label }}</label>
                <input v-model.number="spec.launch[key]" type="number" class="form-control form-control-sm" placeholder="default" />
              </div>
              <div class="col-6 col-md-3">
                <label class="form-label small">Freeze check mode</label>
                <select v-model="spec.launch.freeze_check_mode" class="form-select form-select-sm">
                  <option :value="null">default</option>
                  <option value="crash">crash</option>
                  <option value="disabled">disabled</option>
                </select>
              </div>
              <div class="col-6 col-md-3">
                <label class="form-label small">Debugger address</label>
                <input v-model="spec.launch.debugger_address" class="form-control form-control-sm" placeholder="(off)" />
              </div>
              <div class="col-6 col-md-3">
                <label class="form-label small">Load session save</label>
                <input v-model="spec.launch.load_session_save" class="form-control form-control-sm" placeholder="(latest)" />
              </div>
            </div>
            <div class="d-flex gap-4 flex-wrap mt-3">
              <div v-for="[key, label] in launchSwitchFields" :key="key" class="form-check">
                <input :id="'lp_' + key" v-model="spec.launch[key]" class="form-check-input" type="checkbox" />
                <label :for="'lp_' + key" class="form-check-label small">{{ label }}</label>
              </div>
            </div>
            <div class="mt-3">
              <label class="form-label small">Extra launch arguments <small class="text-secondary">(raw, appended verbatim)</small></label>
              <input v-model="spec.launch.extra_args" class="form-control form-control-sm" placeholder="-someArg value" />
            </div>
          </div>

          <button class="btn btn-link px-0 mt-3" @click="showAccess = !showAccess">
            {{ showAccess ? '▾ Hide' : '▸ Show' }} player access
          </button>
          <!-- Collapsed is the default, so the toggle has to say what is set —
               a hidden whitelist is how someone locks themselves out (#154). -->
          <span
            v-if="accessSummary"
            class="small ms-2"
            :class="spec.player_whitelist.length ? 'text-warning-emphasis' : 'text-secondary'"
          >{{ accessSummary }}</span>

          <!-- Server admins, whitelist and ban list (#154) -->
          <div v-show="showAccess" class="border-top pt-3">
            <p class="small text-secondary">
              Who administers this server, and who may join it. Everything here is
              part of the template, so every server instance built from it gets the
              same lists.
            </p>

            <!-- Admins -->
            <div class="mb-4">
              <label class="form-label mb-1">
                Server admins
                <small class="text-secondary">
                  ({{ spec.admins.length }} of {{ ADMIN_LIMIT }})
                </small>
              </label>
              <p class="small text-secondary mb-2">
                Admins can <code>#login</code> without the admin password and use the
                priority join queue. Accepts a Steam64 id (17 digits) or a Bohemia
                identity id — paste several at once if you like.
              </p>
              <div class="input-group">
                <input
                  v-model="adminInput"
                  class="form-control"
                  placeholder="76561198000000000 or 7f9b0a4c-1d2e-4f6a-8b3c-9d0e1f2a3b4c"
                  :disabled="locked"
                  @keyup.enter="addAdminIds"
                />
                <button class="btn btn-outline-primary" :disabled="locked || !adminInput.trim()" @click="addAdminIds">
                  Add
                </button>
              </div>
              <div v-if="adminNotice" class="form-text">{{ adminNotice }}</div>
              <div v-if="adminsOverLimit" class="alert alert-warning py-2 small mt-2 mb-0">
                This template lists {{ spec.admins.length }} admins. Arma Reforger
                applies at most {{ ADMIN_LIMIT }} — remove some, or the ones past the
                limit may be ignored.
              </div>
              <div v-if="spec.admins.length" class="d-flex flex-wrap gap-2 mt-2">
                <span
                  v-for="id in spec.admins"
                  :key="id"
                  class="badge d-inline-flex align-items-center gap-2 text-bg-secondary"
                >
                  <span class="font-monospace">{{ id }}</span>
                  <small class="opacity-75">{{ idKind(id) === 'steam' ? 'Steam' : 'identity' }}</small>
                  <button
                    type="button"
                    class="btn-close btn-close-white"
                    style="font-size: 0.55rem"
                    aria-label="Remove admin"
                    :disabled="locked"
                    @click="dropAdmin(id)"
                  ></button>
                </span>
              </div>
              <div v-else class="small text-secondary mt-2">
                No admins listed — only the admin password grants admin rights.
              </div>
            </div>

            <!-- Whitelist -->
            <div class="mb-4">
              <label class="form-label mb-1">Whitelist</label>
              <p class="small text-secondary mb-2">
                Leave this empty and everyone may join. Add one player and the server
                becomes <strong>whitelist-only</strong> — anyone not listed is refused.
                Bohemia identity ids only; a Steam id will not match.
              </p>
              <div class="row g-2">
                <div class="col-md-6">
                  <input
                    v-model="whitelistInput.id"
                    class="form-control"
                    placeholder="Identity id"
                    :disabled="locked"
                    @keyup.enter="addWhitelistPlayers"
                  />
                </div>
                <div class="col-md-4">
                  <input
                    v-model="whitelistInput.name"
                    class="form-control"
                    placeholder="Name (optional)"
                    :disabled="locked"
                    @keyup.enter="addWhitelistPlayers"
                  />
                </div>
                <div class="col-md-2 d-grid">
                  <button
                    class="btn btn-outline-primary"
                    :disabled="locked || !whitelistInput.id.trim()"
                    @click="addWhitelistPlayers"
                  >
                    Add
                  </button>
                </div>
              </div>
              <div v-if="whitelistNotice" class="form-text">{{ whitelistNotice }}</div>
              <div v-if="spec.player_whitelist.length" class="alert alert-warning py-2 small mt-2 mb-2">
                Whitelist is <strong>active</strong>: only these
                {{ spec.player_whitelist.length }}
                {{ spec.player_whitelist.length === 1 ? 'player' : 'players' }} can join.
                Make sure your own identity id is on it.
              </div>
              <ul v-if="spec.player_whitelist.length" class="list-group mt-2">
                <li
                  v-for="p in spec.player_whitelist"
                  :key="p.identityId"
                  class="list-group-item d-flex align-items-center justify-content-between py-2"
                >
                  <span>
                    <span class="fw-semibold">{{ p.name || '(no name)' }}</span>
                    <span class="font-monospace small text-secondary ms-2">{{ p.identityId }}</span>
                  </span>
                  <button
                    class="btn btn-sm btn-outline-secondary"
                    :disabled="locked"
                    @click="dropWhitelistPlayer(p.identityId)"
                  >
                    Remove
                  </button>
                </li>
              </ul>
            </div>

            <!-- Ban list -->
            <div>
              <label class="form-label mb-1">Ban list</label>
              <p class="small text-secondary mb-2">
                Players refused entry, by Bohemia identity id. Bans written here apply
                when the server starts; to ban someone mid-session use the in-game
                admin tools or RCON.
              </p>
              <div class="row g-2">
                <div class="col-md-4">
                  <input
                    v-model="banInput.id"
                    class="form-control"
                    placeholder="Identity id"
                    :disabled="locked"
                    @keyup.enter="addBannedPlayers"
                  />
                </div>
                <div class="col-md-3">
                  <input
                    v-model="banInput.name"
                    class="form-control"
                    placeholder="Name (optional)"
                    :disabled="locked"
                    @keyup.enter="addBannedPlayers"
                  />
                </div>
                <div class="col-md-3">
                  <input
                    v-model="banInput.reason"
                    class="form-control"
                    placeholder="Reason (optional)"
                    :disabled="locked"
                    @keyup.enter="addBannedPlayers"
                  />
                </div>
                <div class="col-md-2 d-grid">
                  <button
                    class="btn btn-outline-danger"
                    :disabled="locked || !banInput.id.trim()"
                    @click="addBannedPlayers"
                  >
                    Ban
                  </button>
                </div>
              </div>
              <div v-if="banNotice" class="form-text">{{ banNotice }}</div>
              <ul v-if="spec.player_ban_list.length" class="list-group mt-2">
                <li
                  v-for="p in spec.player_ban_list"
                  :key="p.identityId"
                  class="list-group-item d-flex align-items-center justify-content-between py-2"
                >
                  <span>
                    <span class="fw-semibold">{{ p.name || '(no name)' }}</span>
                    <span class="font-monospace small text-secondary ms-2">{{ p.identityId }}</span>
                    <span v-if="p.reason" class="small text-secondary ms-2">— {{ p.reason }}</span>
                  </span>
                  <button
                    class="btn btn-sm btn-outline-secondary"
                    :disabled="locked"
                    @click="dropBannedPlayer(p.identityId)"
                  >
                    Unban
                  </button>
                </li>
              </ul>
              <div v-else class="small text-secondary mt-2">Nobody is banned.</div>
            </div>
          </div>

          <button class="btn btn-link px-0 mt-3" @click="showMissionHeader = !showMissionHeader">
            {{ showMissionHeader ? '▾ Hide' : '▸ Show' }} mission header
          </button>
          <!-- Collapsed by default like player access, so the toggle has to say
               whether anything is set (#162). -->
          <span v-if="missionHeaderCount" class="small ms-2 text-secondary">
            {{ missionHeaderCount }} override{{ missionHeaderCount === 1 ? '' : 's' }}
          </span>

          <!-- Mission header overrides (#162) -->
          <div v-show="showMissionHeader" class="border-top pt-3">
            <p class="small text-secondary">
              Overrides for the scenario's own mission header — player count, XP rate,
              time of day, campaign supplies. Which settings a scenario accepts depends
              on the scenario, and a mod can add its own; anything you type is written
              through, whether or not it's on the list below.
            </p>

            <div class="d-flex flex-wrap gap-2 align-items-center mb-3">
              <div class="btn-group btn-group-sm" role="group">
                <button
                  class="btn"
                  :class="headerView === 'rows' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="headerView = 'rows'"
                >
                  Settings
                </button>
                <button
                  class="btn"
                  :class="headerView === 'json' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="openHeaderJson"
                >
                  JSON
                </button>
              </div>
              <button class="btn btn-sm btn-outline-primary" :disabled="locked" @click="openPaste">
                Paste from AMP or a config…
              </button>
            </div>

            <!-- Rows -->
            <template v-if="headerView === 'rows'">
              <div v-if="!headerRows.length" class="small text-secondary mb-3">
                No overrides — the scenario's own values are used.
              </div>
              <div v-for="(row, i) in headerRows" :key="i" class="row g-2 align-items-start mb-2">
                <div class="col-md-4">
                  <input
                    v-model="row.key"
                    class="form-control form-control-sm font-monospace"
                    :disabled="locked"
                    @change="commitHeaderRows"
                  />
                  <div v-if="describeRow(row.key)" class="form-text">
                    {{ describeRow(row.key).help }}
                    <span v-if="describeRow(row.key).caveat" class="text-warning-emphasis">
                      {{ describeRow(row.key).caveat }}
                    </span>
                  </div>
                </div>
                <div class="col-md-2">
                  <select
                    class="form-select form-select-sm"
                    :value="row.kind"
                    :disabled="locked || row.kind === 'json'"
                    @change="changeHeaderRowKind(row, $event.target.value)"
                  >
                    <option value="number">Number</option>
                    <option value="string">Text</option>
                    <option value="boolean">On / off</option>
                    <option v-if="row.kind === 'json'" value="json">Block</option>
                  </select>
                </div>
                <div class="col-md-5">
                  <input
                    v-if="row.kind === 'number'"
                    v-model.number="row.value"
                    type="number"
                    step="any"
                    class="form-control form-control-sm"
                    :disabled="locked"
                    @change="commitHeaderRows"
                  />
                  <input
                    v-else-if="row.kind === 'string'"
                    v-model="row.value"
                    class="form-control form-control-sm"
                    :disabled="locked"
                    @change="commitHeaderRows"
                  />
                  <div v-else-if="row.kind === 'boolean'" class="form-check mt-1">
                    <input
                      :id="'mh-' + i"
                      v-model="row.value"
                      class="form-check-input"
                      type="checkbox"
                      :disabled="locked"
                      @change="commitHeaderRows"
                    />
                    <label :for="'mh-' + i" class="form-check-label small">
                      {{ row.value ? 'On' : 'Off' }}
                    </label>
                  </div>
                  <!-- A nested block (a mod's settings tree) is edited as JSON
                       rather than flattened into rows that lie about its shape. -->
                  <div v-else class="d-flex align-items-center gap-2">
                    <span class="badge text-bg-secondary font-monospace">
                      {{ nestedSummary(row.value) }}
                    </span>
                    <button class="btn btn-sm btn-link px-0" @click="openHeaderJson">
                      Edit as JSON
                    </button>
                  </div>
                </div>
                <div class="col-md-1 d-grid">
                  <button
                    class="btn btn-sm btn-outline-secondary"
                    :disabled="locked"
                    title="Remove this override"
                    @click="dropHeaderRow(i)"
                  >
                    ✕
                  </button>
                </div>
              </div>

              <div v-if="headerMissingPrereqs.length" class="alert alert-warning py-2 small">
                <div v-for="key in headerMissingPrereqs" :key="key">
                  <code>{{ key }}</code> has to be on before those settings do anything.
                  <button class="btn btn-sm btn-link p-0 align-baseline" @click="addPrereq(key)">
                    Switch it on
                  </button>
                </div>
              </div>

              <!-- Add a setting -->
              <div class="position-relative" style="max-width: 32rem">
                <input
                  v-model="headerPick.query"
                  class="form-control form-control-sm"
                  placeholder="Add a setting — search, or type any name"
                  :disabled="locked"
                  @focus="headerPick.open = true"
                  @keyup.enter="addHeaderRow(headerMatches[0], headerPick.query)"
                />
                <div
                  v-if="headerPick.open && (headerMatches.length || headerPick.query.trim())"
                  class="list-group position-absolute w-100 shadow"
                  style="z-index: 5; max-height: 20rem; overflow: auto"
                >
                  <button
                    v-for="entry in headerMatches"
                    :key="entry.key"
                    class="list-group-item list-group-item-action py-2"
                    @click="addHeaderRow(entry)"
                  >
                    <div class="d-flex justify-content-between gap-2">
                      <code>{{ entry.key }}</code>
                      <small class="text-secondary">{{ headerGroupLabel[entry.group] }}</small>
                    </div>
                    <div class="small text-secondary">
                      {{ entry.help }}
                      <span v-if="entry.default !== undefined">
                        Default {{ JSON.stringify(entry.default) }}.
                      </span>
                    </div>
                  </button>
                  <button
                    v-if="headerPick.query.trim()"
                    class="list-group-item list-group-item-action py-2"
                    @click="addHeaderRow(null, headerPick.query)"
                  >
                    Add <code>{{ headerPick.query.trim() }}</code> — a setting this list
                    doesn't know about
                  </button>
                </div>
              </div>
              <div class="form-text">
                Names and defaults come from the published mission header classes. A
                scenario or mod can accept others.
              </div>
            </template>

            <!-- Raw JSON -->
            <template v-else>
              <JsonEditor v-model="headerJson" max-height="24rem" />
              <div v-if="headerJsonError" class="alert alert-danger py-1 px-2 my-2 small">
                {{ headerJsonError }}
              </div>
              <div class="d-flex gap-2 mt-2">
                <button class="btn btn-sm btn-primary" :disabled="locked" @click="applyHeaderJson">
                  Apply
                </button>
                <button class="btn btn-sm btn-outline-secondary" @click="headerView = 'rows'">
                  Back to settings
                </button>
              </div>
              <div class="form-text">
                Applying tidies the indentation and repairs the usual copy-paste
                damage — trailing commas, missing braces, curly quotes.
              </div>
            </template>
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
            <button class="btn btn-primary" :disabled="!canNext || saving || locked" @click="save">
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

      <!-- Live config.json preview, and the hand editor it turns into (#29) -->
      <div class="col-lg-5">
        <div class="card position-sticky" style="top: 1rem">
          <div class="card-header d-flex justify-content-between align-items-center py-2 gap-2">
            <span class="small fw-semibold">
              config.json {{ rawMode ? 'editor' : 'preview' }}
            </span>
            <div class="d-flex align-items-center gap-2">
              <span
                v-if="extrasCount"
                class="badge text-bg-info"
                :title="'This template carries ' + extrasCount + ' hand-edited key(s) the GUI does not manage. They are preserved on every save.'"
              >
                {{ extrasCount }} custom key{{ extrasCount === 1 ? '' : 's' }}
              </span>
              <span v-if="!rawMode && spec.scenario_id" class="badge text-bg-secondary">
                scenario set
              </span>
              <template v-if="rawMode">
                <button class="btn btn-sm btn-outline-secondary" @click="cancelRawEdit">
                  Cancel
                </button>
                <button class="btn btn-sm btn-primary" :disabled="!canApply" @click="applyRawEdit">
                  {{ applying ? 'Applying…' : 'Apply' }}
                </button>
              </template>
              <button
                v-else
                class="btn btn-sm btn-outline-primary"
                :disabled="!spec.scenario_id"
                title="Edit the raw config.json — including keys this GUI doesn't manage"
                @click="startRawEdit"
              >
                Edit JSON
              </button>
            </div>
          </div>

          <!-- Editing -->
          <template v-if="rawMode">
            <JsonEditor v-model="draft" max-height="60vh" />
            <div class="card-body py-2 small">
              <div v-if="rawSyntaxError" class="alert alert-danger py-1 px-2 mb-2 small">
                <strong>Invalid JSON:</strong> {{ rawSyntaxError }}
              </div>
              <div v-for="e in rawErrors" :key="e.path" class="alert alert-danger py-1 px-2 mb-2 small">
                <code v-if="e.path" class="text-danger">{{ e.path }}</code>
                {{ e.message }}
              </div>
              <div v-if="rawWarnings.length" class="alert alert-warning py-1 px-2 mb-2 small">
                <div class="fw-semibold mb-1">
                  {{ rawWarnings.length }} key(s) this GUI doesn't manage — kept as-is:
                </div>
                <ul class="mb-0 ps-3">
                  <li v-for="w in rawWarnings" :key="w.path"><code>{{ w.path }}</code></li>
                </ul>
              </div>
              <p
                v-if="!rawSyntaxError && !rawErrors.length && !rawWarnings.length"
                class="text-secondary mb-0"
              >
                Valid. Custom and scenario-specific keys are allowed — they'll be preserved.
              </p>
            </div>
          </template>

          <!-- Previewing -->
          <pre
            v-else
            class="card-body bg-black text-light small mb-0 rounded-bottom"
            style="max-height: 70vh; overflow: auto; white-space: pre-wrap"
          >{{ preview || '// pick a scenario to see the config' }}</pre>
        </div>
      </div>
    </div>

    <!-- Paste a mission header from anywhere (#162) -->
    <div v-if="paste.open" class="rsm-modal-backdrop" @click.self="paste.open = false">
      <div class="card rsm-modal shadow" style="max-width: 46rem">
        <div class="card-body">
          <h2 class="h6">Paste a mission header</h2>
          <p class="small text-secondary mb-2">
            Paste AMP's <code>"m_iPlayerCount":"64"</code> box, a whole
            <code>config.json</code>, or any snippet you were given. Indentation,
            comments, trailing commas, missing braces and curly quotes are all sorted
            out for you — nothing is inserted until you say so.
          </p>
          <textarea
            v-model="paste.text"
            class="form-control font-monospace small"
            rows="10"
            spellcheck="false"
            placeholder='"m_iPlayerCount": 64, "m_fXpMultiplier": 10'
          ></textarea>

          <div v-if="paste.error" class="alert alert-danger py-1 px-2 mt-2 mb-0 small">
            {{ paste.error }}
          </div>

          <template v-if="pasteHeader">
            <div v-if="paste.repairs.length" class="alert alert-info py-2 px-2 mt-2 mb-2 small">
              <div class="fw-semibold mb-1">
                Tidied up {{ paste.repairs.length }} thing{{ paste.repairs.length === 1 ? '' : 's' }}:
              </div>
              <ul class="mb-0 ps-3">
                <li v-for="(note, i) in paste.repairs" :key="i">{{ note }}</li>
              </ul>
            </div>

            <div v-if="paste.quoted.length" class="form-check small mt-2">
              <input
                id="mh-convert"
                v-model="paste.convertQuoted"
                class="form-check-input"
                type="checkbox"
              />
              <label for="mh-convert" class="form-check-label">
                Convert {{ paste.quoted.length }} quoted number{{ paste.quoted.length === 1 ? '' : 's' }}
                (<code>"64"</code> → <code>64</code>). AMP writes them quoted; the
                documented examples don't. Left as pasted unless you tick this.
              </label>
            </div>

            <div class="d-flex flex-wrap gap-3 align-items-center mt-3">
              <div class="form-check">
                <input id="mh-merge" v-model="paste.mode" class="form-check-input" type="radio" value="merge" />
                <label for="mh-merge" class="form-check-label small">Merge into what's there</label>
              </div>
              <div class="form-check">
                <input id="mh-replace" v-model="paste.mode" class="form-check-input" type="radio" value="replace" />
                <label for="mh-replace" class="form-check-label small">Replace everything</label>
              </div>
            </div>

            <p v-if="pastePreview" class="small text-secondary mt-2 mb-0">
              {{ pastePreview.added.length }} added,
              {{ pastePreview.changed.length }} changed,
              {{ pastePreview.unchanged.length }} unchanged<template
                v-if="paste.mode === 'replace' && pastePreview.removed.length"
              >, {{ pastePreview.removed.length }} removed</template>.
            </p>
          </template>

          <div class="d-flex gap-2 justify-content-end mt-3">
            <button class="btn btn-sm btn-outline-secondary" @click="paste.open = false">Cancel</button>
            <button class="btn btn-sm btn-primary" :disabled="!pasteHeader" @click="confirmPaste">
              Insert
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Replace-scenario confirmation (issue #59) -->
    <div v-if="replacePrompt" class="rsm-modal-backdrop" @click.self="replacePrompt = null">
      <div class="card rsm-modal shadow">
        <div class="card-body">
          <h2 class="h6">Replace the current scenario?</h2>
          <p class="small mb-2">
            <span class="text-secondary">Current:</span> {{ scenarioDisplayName }}<br />
            <span class="text-secondary">New:</span> {{ replacePrompt.sc.name }}
            <span class="text-secondary">(from "{{ replacePrompt.res.asset.name }}")</span>
          </p>
          <p v-if="scenarioDroppedMods.length" class="small text-secondary mb-1">
            {{ scenarioDroppedMods.length }} mod(s) that only the current scenario needs will
            be removed (your own mods and shared dependencies are kept):
          </p>
          <ul v-if="scenarioDroppedMods.length" class="small mb-3" style="max-height: 10rem; overflow-y: auto">
            <li v-for="m in scenarioDroppedMods" :key="m.modId">{{ m.name || m.modId }}</li>
          </ul>
          <p class="small text-secondary mb-3">
            The new scenario adds {{ replacePrompt.res.mods.length }} mod(s) —
            review them on the Mods step before saving.
          </p>
          <div class="d-flex flex-wrap gap-2 justify-content-end">
            <button class="btn btn-sm btn-outline-secondary" @click="replacePrompt = null">Cancel</button>
            <button class="btn btn-sm btn-primary" @click="applyScenario(replacePrompt.sc, replacePrompt.res)">
              Replace scenario
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Remove-scenario confirmation (issue #59) -->
    <div v-if="removeScenarioPrompt" class="rsm-modal-backdrop" @click.self="removeScenarioPrompt = false">
      <div class="card rsm-modal shadow">
        <div class="card-body">
          <h2 class="h6">Remove "{{ scenarioDisplayName }}"?</h2>
          <p v-if="scenarioDroppedMods.length" class="small text-secondary mb-1">
            {{ scenarioDroppedMods.length }} mod(s) it brought in will be removed with it
            (your own mods and shared dependencies are kept):
          </p>
          <ul v-if="scenarioDroppedMods.length" class="small mb-2" style="max-height: 10rem; overflow-y: auto">
            <li v-for="m in scenarioDroppedMods" :key="m.modId">{{ m.name || m.modId }}</li>
          </ul>
          <p class="small text-secondary mb-3">
            A template needs a scenario, so you'll have to pick a new one before this
            template can be saved. Nothing changes until you save.
          </p>
          <div class="d-flex flex-wrap gap-2 justify-content-end">
            <button class="btn btn-sm btn-outline-secondary" @click="removeScenarioPrompt = false">Cancel</button>
            <button class="btn btn-sm btn-danger" @click="removeScenario">Remove scenario</button>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>

<style scoped>
.rsm-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1050;
  padding: 1rem;
}
.rsm-modal {
  width: 100%;
  max-width: 32rem;
}
</style>
