import { beforeEach, describe, expect, it, vi } from 'vitest'

import { configureChunkRecovery, loadRouteChunk, reloadCurrentApplication } from '../src/chunkRecovery'
import type { ChunkRecoveryError } from '../src/chunkRecovery'

const build = 'a'.repeat(64)

describe('route chunk recovery', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
    configureChunkRecovery(build)
  })

  it('reloads at most once for the current build and then exposes persistent failure', async () => {
    let attempted: string | undefined
    const reload = vi.fn()
    const runtime = {
      attemptedBuild: () => attempted,
      markAttempt: (identity: string) => { attempted = identity },
      reload,
    }
    const missing = () => Promise.reject(new TypeError('missing chunk'))

    await expect(loadRouteChunk(missing, build, runtime)).rejects.toMatchObject({ exhausted: false })
    expect(attempted).toBe(build)
    expect(reload).toHaveBeenCalledOnce()

    await expect(loadRouteChunk(missing, build, runtime)).rejects.toEqual(expect.objectContaining<Partial<ChunkRecoveryError>>({ exhausted: true }))
    expect(reload).toHaveBeenCalledOnce()
  })

  it('keeps the one-attempt marker after a successful recovered import', async () => {
    let attempted = build
    const reload = vi.fn()
    const runtime = {
      attemptedBuild: () => attempted,
      markAttempt: (identity: string) => { attempted = identity },
      reload,
    }

    await expect(loadRouteChunk(async () => ({ default: 'loaded' }), build, runtime)).resolves.toEqual({ default: 'loaded' })
    await expect(loadRouteChunk(() => Promise.reject(new Error('later missing chunk')), build, runtime)).rejects.toMatchObject({ exhausted: true })
    expect(reload).not.toHaveBeenCalled()
  })

  it('marks the current build before an explicit recovery reload', () => {
    let attempted: string | undefined
    const reload = vi.fn()
    const runtime = {
      attemptedBuild: () => attempted,
      markAttempt: (identity: string) => { attempted = identity },
      reload,
    }

    reloadCurrentApplication(build, runtime)

    expect(attempted).toBe(build)
    expect(reload).toHaveBeenCalledOnce()
  })
})
