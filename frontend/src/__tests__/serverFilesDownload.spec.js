import { describe, expect, it, vi } from 'vitest'
import { ApiError, errorMessage } from '../api'
import { startServerFilesDownload } from '../jobs'

const refusal = () =>
  new ApiError(409, { message: 'These stable servers are running', running_servers: ['Alpha', 'Bravo'] })

describe('startServerFilesDownload', () => {
  it('starts the download when no server is running on the files', async () => {
    const request = vi.fn().mockResolvedValue({ status: 'running' })
    const ask = vi.fn()
    expect(await startServerFilesDownload('stable', { request, ask })).toEqual({ status: 'running' })
    expect(request).toHaveBeenCalledTimes(1)
    expect(ask).not.toHaveBeenCalled()
  })

  it('names the running servers, and downloads on a yes', async () => {
    const request = vi.fn().mockRejectedValueOnce(refusal()).mockResolvedValueOnce({ status: 'running' })
    const ask = vi.fn().mockReturnValue(true)
    expect(await startServerFilesDownload('stable', { request, ask })).toEqual({ status: 'running' })
    expect(ask.mock.calls[0][0]).toContain('Alpha\nBravo')
    expect(request).toHaveBeenLastCalledWith('/api/serverfiles/stable/download', {
      method: 'POST',
      body: { force: true },
    })
  })

  it('starts nothing on a no', async () => {
    const request = vi.fn().mockRejectedValueOnce(refusal())
    expect(await startServerFilesDownload('stable', { request, ask: () => false })).toBeNull()
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('passes any other refusal on', async () => {
    const request = vi.fn().mockRejectedValue(new ApiError(409, 'A stable download is already running'))
    await expect(startServerFilesDownload('stable', { request, ask: vi.fn() })).rejects.toThrow(
      'already running',
    )
  })
})

describe('errorMessage', () => {
  it('reads the message of a refusal that carries data', () => {
    expect(errorMessage({ message: 'Stop them first', running_servers: ['A'] }, 409)).toBe('Stop them first')
  })
})
