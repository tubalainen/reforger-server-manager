import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useBranchDownload, useImagePull } from '../jobs'

// A WebSocket the test drives by hand: send() an event, close() the socket.
class FakeSocket {
  static last = null
  constructor(url) {
    this.url = url
    FakeSocket.last = this
  }
  send(event) {
    this.onmessage({ data: JSON.stringify(event) })
  }
  close() {
    this.onclose()
  }
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('location', { protocol: 'https:', host: 'rsm.example' })
})
afterEach(() => vi.unstubAllGlobals())

describe('useImagePull', () => {
  it('follows a pull from snapshot to success', () => {
    const onUpdate = vi.fn()
    const onClose = vi.fn()
    const pull = useImagePull('/api/serverfiles/steamcmd-image', { onUpdate, onClose })
    pull.connect()
    const ws = FakeSocket.last
    expect(ws.url).toBe('wss://rsm.example/api/serverfiles/steamcmd-image/ws')

    ws.send({ type: 'snapshot', job: { status: 'running', percent: 0 }, present: false, log: ['a'] })
    ws.send({ type: 'progress', phase: 'Downloading', percent: 40 })
    ws.send({ type: 'log', line: 'b' })
    expect(pull.state.job.percent).toBe(40)
    expect(pull.state.log).toEqual(['a', 'b'])

    // `present: null` is "not known yet" and keeps what we had.
    ws.send({ type: 'status', job: { status: 'running' }, present: null })
    expect(pull.state.present).toBe(false)
    ws.send({ type: 'status', job: { status: 'success' }, present: true })
    expect(pull.state.present).toBe(true)
    expect(onUpdate).toHaveBeenCalledTimes(5)

    pull.close()
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('opens one socket however often it is asked', () => {
    const pull = useImagePull('/api/serverfiles/image')
    pull.connect()
    const first = FakeSocket.last
    pull.connect()
    expect(FakeSocket.last).toBe(first)
  })
})

describe('useBranchDownload', () => {
  it('keeps the installed build until a new one is reported', () => {
    const dl = useBranchDownload('stable')
    dl.connect()
    const ws = FakeSocket.last
    expect(ws.url).toBe('wss://rsm.example/api/serverfiles/stable/ws')

    ws.send({
      type: 'snapshot',
      state: { job: { status: 'running' }, installed: { build_id: '1' } },
      log: [],
    })
    ws.send({ type: 'status', job: { status: 'running' }, installed: null })
    expect(dl.state.installed).toEqual({ build_id: '1' })
    ws.send({ type: 'status', job: { status: 'success' }, installed: { build_id: '2' } })
    expect(dl.state.installed).toEqual({ build_id: '2' })
    expect(dl.state.job.status).toBe('success')
  })

  it('caps the log', () => {
    const dl = useBranchDownload('stable')
    dl.connect()
    for (let i = 0; i < 510; i++) FakeSocket.last.send({ type: 'log', line: String(i) })
    expect(dl.state.log).toHaveLength(500)
    expect(dl.state.log[0]).toBe('10')
  })
})
