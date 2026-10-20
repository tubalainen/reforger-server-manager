// Live progress of the manager's long downloads, over each job's WebSocket: an
// image pull (the server runtime image, #50, or the SteamCMD image, #241) and a
// branch's server files. System › Server files and the first-run setup screen
// both show them, so the socket handling lives here once.
import { reactive } from 'vue'
import { api } from './api'

// Lines kept for the log pane; the backend keeps more for late joiners.
const LOG_LINES = 500

function socketUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}${path}`
}

function pushLine(log, line) {
  log.push(line)
  if (log.length > LOG_LINES) log.shift()
}

/**
 * One image pull under `base` (`/api/serverfiles/image`, …): `state` is
 * { present, job, log }. `onUpdate` runs after every event (to scroll a log
 * pane), `onClose` when the socket ends (to reload the page's status).
 */
export function useImagePull(base, { onUpdate, onClose } = {}) {
  const state = reactive({ present: false, job: null, log: [] })
  let socket = null

  function connect() {
    if (socket) return
    const ws = new WebSocket(socketUrl(`${base}/ws`))
    socket = ws
    ws.onmessage = (msg) => {
      const ev = JSON.parse(msg.data)
      if (ev.type === 'snapshot') {
        state.job = ev.job
        state.present = ev.present
        state.log = ev.log || []
      } else if (ev.type === 'log') {
        pushLine(state.log, ev.line)
      } else if (ev.type === 'progress') {
        if (state.job) Object.assign(state.job, ev)
      } else if (ev.type === 'status') {
        state.job = ev.job
        if (ev.present !== null) state.present = ev.present
      }
      onUpdate?.()
    }
    ws.onclose = () => {
      socket = null
      onClose?.()
    }
  }

  async function pull() {
    state.job = await api(`${base}/pull`, { method: 'POST' })
    state.log = []
    connect()
  }

  return { state, connect, pull, close: () => socket?.close() }
}

/**
 * One branch's server-file download: `state` is { job, installed, log }, with
 * the same callbacks as useImagePull.
 */
export function useBranchDownload(branch, { onUpdate, onClose } = {}) {
  const state = reactive({ job: null, installed: null, log: [] })
  let socket = null

  function connect() {
    if (socket) return
    const ws = new WebSocket(socketUrl(`/api/serverfiles/${branch}/ws`))
    socket = ws
    ws.onmessage = (msg) => {
      const ev = JSON.parse(msg.data)
      if (ev.type === 'snapshot') {
        state.job = ev.state.job
        state.installed = ev.state.installed
        state.log = ev.log || []
      } else if (ev.type === 'log') {
        pushLine(state.log, ev.line)
      } else if (ev.type === 'progress') {
        if (state.job) Object.assign(state.job, ev)
      } else if (ev.type === 'status') {
        state.job = ev.job
        if (ev.installed) state.installed = ev.installed
      }
      onUpdate?.()
    }
    ws.onclose = () => {
      socket = null
      onClose?.()
    }
  }

  async function download() {
    state.job = await api(`/api/serverfiles/${branch}/download`, { method: 'POST' })
    state.log = []
    connect()
  }

  return { state, connect, download, close: () => socket?.close() }
}
