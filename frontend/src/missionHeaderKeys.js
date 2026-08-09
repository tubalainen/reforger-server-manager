// The mission header settings we can offer by name (#162).
//
// These are members of the scenario's mission header class. The base class,
// SCR_MissionHeader, is what every scenario has; SCR_MissionHeaderCampaign
// inherits it and adds the campaign-only ones. Names, types and defaults below
// are read off the published script sources for those classes, which is why
// each entry can pre-fill the engine's own default instead of guessing:
//   https://arexplorer.zeroy.com/_s_c_r___mission_header_8c_source.html
//   https://arexplorer.zeroy.com/_s_c_r___mission_header_campaign_8c_source.html
//
// Three things this list deliberately does NOT do:
//
//   * It is not a whitelist. A mod's header class adds its own settings (ACE
//     nests a whole tree under m_ACE_Settings), and a scenario can be built on
//     any header class at all. Anything typed goes through; this list only
//     saves people looking names up.
//   * It does not promise a setting works. Which ones actually take effect
//     depends on the scenario — players report the time-of-day settings being
//     ignored by the stock campaign while the flag and XP ones work
//     (https://forums.bohemia.net/forums/topic/359610-missionheader/). Entries
//     carry `caveat` where that's known, and the UI shows it.
//   * It does not decode the flag enums. m_eSaveTypes (ESaveGameType) and
//     m_e*GameFlags (EGameFlags) are bit masks whose members aren't published
//     anywhere we could verify, so they stay plain numbers with their engine
//     default rather than a checkbox helper built on a guess.

export const GROUPS = [
  { id: 'general', label: 'Any scenario' },
  { id: 'time', label: 'Time and weather' },
  { id: 'campaign', label: 'Campaign only' },
]

export const MISSION_HEADER_KEYS = [
  // ---- SCR_MissionHeader: shown to players -------------------------------
  { key: 'm_sName', kind: 'string', group: 'general',
    help: 'Scenario name players see in the browser.' },
  { key: 'm_sAuthor', kind: 'string', group: 'general',
    help: 'Scenario author.' },
  { key: 'm_sDescription', kind: 'string', group: 'general',
    help: 'Short description of the scenario.' },
  { key: 'm_sDetails', kind: 'string', group: 'general',
    help: 'Longer text — the usual home for server rules.' },

  // ---- SCR_MissionHeader: gameplay ---------------------------------------
  { key: 'm_iPlayerCount', kind: 'number', default: 1, group: 'general',
    help: 'Player count the scenario declares. Does not raise the server\'s own player limit.' },
  { key: 'm_fXpMultiplier', kind: 'number', default: 1, group: 'general',
    help: 'XP multiplier. 1 is normal, 10 is ten times.' },
  { key: 'm_eSaveTypes', kind: 'number', default: 15, group: 'general',
    help: 'Which save types the scenario allows, as a bit mask. 0 stops it saving at all.' },
  { key: 'm_eEditableGameFlags', kind: 'number', default: 0, group: 'general',
    help: 'Game flags players may change in the lobby, as a bit mask.' },
  { key: 'm_eDefaultGameFlags', kind: 'number', default: 0, group: 'general',
    help: 'Game flags the scenario starts with, as a bit mask.' },
  { key: 'm_bMapMarkerEnableDeleteByAnyone', kind: 'boolean', default: false, group: 'general',
    help: 'Let anyone delete a map marker, not only whoever placed it.' },
  { key: 'm_iMapMarkerLimitPerPlayer', kind: 'number', default: 10, group: 'general',
    help: 'Map markers a single player may have placed at once.' },
  { key: 'm_bIsArmavisionAllowedInMP', kind: 'boolean', default: false, group: 'general',
    help: 'Allow Armavision in multiplayer.' },
  { key: 'm_sGameMode', kind: 'string', default: 'Sandbox', group: 'general',
    help: 'Game mode label the scenario reports.' },
  { key: 'm_bShowInScenarioMenu', kind: 'boolean', default: true, group: 'general',
    help: 'Whether the scenario is listed in the scenario menu.' },

  // ---- SCR_MissionHeader: time and weather --------------------------------
  { key: 'm_bOverrideScenarioTimeAndWeather', kind: 'boolean', default: false, group: 'time',
    help: 'Must be on before any of the time or weather settings below do anything.' },
  { key: 'm_iStartingHours', kind: 'number', default: 8, group: 'time', requires:
    'm_bOverrideScenarioTimeAndWeather',
    help: 'Hour the mission starts at, 0–23.',
    caveat: 'Players report this being ignored by some stock scenarios.' },
  { key: 'm_iStartingMinutes', kind: 'number', default: 0, group: 'time', requires:
    'm_bOverrideScenarioTimeAndWeather',
    help: 'Minute the mission starts at, 0–59.',
    caveat: 'Players report this being ignored by some stock scenarios.' },
  { key: 'm_bRandomStartingDaytime', kind: 'boolean', default: false, group: 'time', requires:
    'm_bOverrideScenarioTimeAndWeather',
    help: 'Randomise the start time, ignoring the hours and minutes above.' },
  { key: 'm_fDayTimeAcceleration', kind: 'number', default: 1, group: 'time',
    help: 'How fast daytime passes. 1 is real time, 6 is six times.' },
  { key: 'm_fNightTimeAcceleration', kind: 'number', default: 1, group: 'time',
    help: 'How fast night passes.' },
  { key: 'm_bRandomStartingWeather', kind: 'boolean', default: false, group: 'time',
    help: 'Pick the starting weather at random.' },
  { key: 'm_bRandomWeatherChanges', kind: 'boolean', default: false, group: 'time',
    help: 'Let the weather change during play.' },

  // ---- SCR_MissionHeaderCampaign -----------------------------------------
  { key: 'm_iControlPointsCap', kind: 'number', default: -1, group: 'campaign',
    help: 'Control points needed to win. -1 keeps the scenario\'s own value.' },
  { key: 'm_fVictoryTimeout', kind: 'number', default: -1, group: 'campaign',
    help: 'Seconds a faction must hold the cap to win. -1 keeps the scenario\'s own value.' },
  { key: 'm_iStartingHQSupplies', kind: 'number', default: -1, group: 'campaign',
    help: 'Supplies the main base starts with. -1 keeps the scenario\'s own value.' },
  { key: 'm_iMinimumBaseSupplies', kind: 'number', group: 'campaign',
    help: 'Lowest starting supplies for a small base.' },
  { key: 'm_iMaximumBaseSupplies', kind: 'number', group: 'campaign',
    help: 'Highest starting supplies for a small base.' },
  { key: 'm_bIgnoreMinimumVehicleRank', kind: 'boolean', group: 'campaign',
    help: 'Drop the rank requirement for spawning vehicles.' },
  { key: 'm_fSupplyOffloadAssistanceReward', kind: 'number', group: 'campaign',
    help: 'Share of the XP given to players helping unload supplies.' },
  { key: 'm_bCommanderRoleEnabled', kind: 'boolean', group: 'campaign',
    help: 'Enable the commander role.' },
  { key: 'm_bEstablishingBasesEnabled', kind: 'boolean', group: 'campaign',
    help: 'Let players establish new bases.' },
  { key: 'm_bSuppliesAutoRegenerationEnabled', kind: 'boolean', group: 'campaign',
    help: 'Regenerate base supplies over time.' },
  { key: 'm_bSpawnRandomCaches', kind: 'boolean', group: 'campaign',
    help: 'Spawn random supply caches around the map.' },
  { key: 'm_bRandomSpawnpointsEnabled', kind: 'boolean', group: 'campaign',
    help: 'Enable random spawn points.' },
  { key: 'm_bINDFORCanSpawnOnBases', kind: 'boolean', group: 'campaign',
    help: 'Let the independent faction spawn on bases.' },
  { key: 'm_bINDFORCanSpawnOnDistantBases', kind: 'boolean', group: 'campaign',
    help: 'Let the independent faction spawn on distant bases too.' },
]

const BY_KEY = new Map(MISSION_HEADER_KEYS.map((entry) => [entry.key, entry]))

/** What we know about a setting, or undefined for one we've never heard of. */
export function describeKey(key) {
  return BY_KEY.get(key)
}

/** Catalog entries matching `query`, minus the ones already in the header.
 *
 * Matched word by word rather than as one substring, because nobody types
 * `m_iStartingHours` — they type "starting hour" or "hour start", and a plain
 * `includes` finds neither in a key that has no spaces in it.
 */
export function searchKeys(query, used = []) {
  const words = String(query ?? '').trim().toLowerCase().split(/\s+/).filter(Boolean)
  const taken = new Set(used)
  return MISSION_HEADER_KEYS.filter((entry) => {
    if (taken.has(entry.key)) return false
    const haystack = `${entry.key} ${entry.help} ${entry.group}`.toLowerCase()
    return words.every((word) => haystack.includes(word))
  })
}

/** The value a freshly added setting starts on: the engine's own default. */
export function defaultValueFor(entry) {
  if (!entry) return ''
  if (entry.default !== undefined) return entry.default
  if (entry.kind === 'number') return 0
  if (entry.kind === 'boolean') return false
  return ''
}
