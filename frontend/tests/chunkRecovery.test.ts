import { act, cleanup, render, screen } from '@testing-library/react'
import { Component, Suspense, createElement } from 'react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ChunkRecoveryError,
  configureChunkRecovery,
  loadRouteChunk,
  recoverableLazy,
  reloadCurrentApplication,
} from '../src/chunkRecovery'

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
      markAttempt: (identity: string) => {
        attempted = identity
      },
      reload,
    }
    const missing = () => Promise.reject(new TypeError('missing chunk'))

    await expect(loadRouteChunk(missing, build, runtime)).rejects.toMatchObject({
      exhausted: false,
    })
    expect(attempted).toBe(build)
    expect(reload).toHaveBeenCalledOnce()

    await expect(loadRouteChunk(missing, build, runtime)).rejects.toEqual(
      expect.objectContaining<Partial<ChunkRecoveryError>>({ exhausted: true }),
    )
    expect(reload).toHaveBeenCalledOnce()
  })

  it('keeps the one-attempt marker after a successful recovered import', async () => {
    let attempted = build
    const reload = vi.fn()
    const runtime = {
      attemptedBuild: () => attempted,
      markAttempt: (identity: string) => {
        attempted = identity
      },
      reload,
    }

    await expect(
      loadRouteChunk(async () => ({ default: 'loaded' }), build, runtime),
    ).resolves.toEqual({ default: 'loaded' })
    await expect(
      loadRouteChunk(() => Promise.reject(new Error('later missing chunk')), build, runtime),
    ).rejects.toMatchObject({ exhausted: true })
    expect(reload).not.toHaveBeenCalled()
  })

  const recordingRuntime = () => {
    let attempted: string | undefined
    const runtime = {
      attemptedBuild: () => attempted,
      markAttempt: (identity: string) => {
        attempted = identity
      },
      reload: vi.fn(),
    }
    return { runtime, reload: runtime.reload, attempted: () => attempted }
  }

  class Boundary extends Component<{ children: ReactNode }, { error: unknown }> {
    override state: { error: unknown } = { error: undefined }
    static getDerivedStateFromError(error: unknown) {
      return { error }
    }
    override render() {
      const { error } = this.state
      if (error === undefined) return this.props.children
      return createElement(
        'p',
        { role: 'alert' },
        error instanceof ChunkRecoveryError && error.exhausted ? 'exhausted' : 'reloading',
      )
    }
  }
  const renderPage = (Page: ReturnType<typeof recoverableLazy>) =>
    render(
      createElement(
        Boundary,
        null,
        createElement(Suspense, { fallback: 'loading' }, createElement(Page)),
      ),
    )

  it('lets an intent preload fail without spending the one recovery reload', async () => {
    const recovery = recordingRuntime()
    const load = vi
      .fn<() => Promise<{ default: () => ReactNode }>>()
      .mockRejectedValueOnce(new TypeError('missing chunk'))
      .mockResolvedValue({ default: () => createElement('p', null, 'Loaded page') })
    const Page = recoverableLazy(load, recovery.runtime)

    await expect(Page.preload?.()).resolves.toBeUndefined()
    expect(recovery.reload).not.toHaveBeenCalled()
    expect(recovery.attempted()).toBeUndefined()

    // A later successful intent fills the router wrapper, so the page renders
    // without suspending.
    await Page.preload?.()
    renderPage(Page)
    expect(screen.getByText('Loaded page')).toBeVisible()
    expect(recovery.reload).not.toHaveBeenCalled()
  })

  it('reloads once for a render-path chunk failure and then shows exhaustion', async () => {
    const recovery = recordingRuntime()
    const missing = () => Promise.reject(new TypeError('missing chunk'))

    await act(async () => {
      renderPage(recoverableLazy(missing, recovery.runtime))
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('reloading')
    expect(recovery.reload).toHaveBeenCalledOnce()
    expect(recovery.attempted()).toBe(build)
    cleanup()

    // The reloaded document builds its wrappers again over the same marker.
    await act(async () => {
      renderPage(recoverableLazy(missing, recovery.runtime))
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('exhausted')
    expect(recovery.reload).toHaveBeenCalledOnce()
  })

  it('marks the current build before an explicit recovery reload', () => {
    let attempted: string | undefined
    const reload = vi.fn()
    const runtime = {
      attemptedBuild: () => attempted,
      markAttempt: (identity: string) => {
        attempted = identity
      },
      reload,
    }

    reloadCurrentApplication(build, runtime)

    expect(attempted).toBe(build)
    expect(reload).toHaveBeenCalledOnce()
  })
})
