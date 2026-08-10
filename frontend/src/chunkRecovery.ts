import { lazy } from 'react'

import type { ComponentType } from 'react'

const stateKey = '__ml_chunk_retry_build'
const buildPattern = /^[0-9a-f]{64}$/

interface ChunkRecoveryRuntime {
  attemptedBuild(): string | undefined
  markAttempt(buildIdentity: string): void
  reload(): void
}

const browserRuntime: ChunkRecoveryRuntime = {
  attemptedBuild: () => {
    const state: unknown = window.history.state
    return typeof state === 'object' && state !== null && !Array.isArray(state)
      && typeof (state as Record<string, unknown>)[stateKey] === 'string'
      ? (state as Record<string, string>)[stateKey]
      : undefined
  },
  markAttempt: (buildIdentity) => {
    const state: unknown = window.history.state
    const current = typeof state === 'object' && state !== null && !Array.isArray(state)
      ? state as Record<string, unknown>
      : {}
    window.history.replaceState({ ...current, [stateKey]: buildIdentity }, '', window.location.href)
  },
  reload: () => window.location.reload(),
}

export class ChunkRecoveryError extends Error {
  constructor(readonly exhausted: boolean) {
    super(exhausted
      ? 'The application update is still unavailable after one safe reload.'
      : 'Reloading once to retrieve the current application update.')
    this.name = 'ChunkRecoveryError'
  }
}

let currentBuildIdentity: string | undefined
let attemptedBuildIdentity: string | undefined

export const configureChunkRecovery = (buildIdentity: string | undefined) => {
  currentBuildIdentity = buildIdentity && buildPattern.test(buildIdentity)
    ? buildIdentity
    : undefined
  attemptedBuildIdentity = browserRuntime.attemptedBuild()
}

export const loadRouteChunk = async <Value>(
  load: () => Promise<Value>,
  buildIdentity = currentBuildIdentity,
  runtime: ChunkRecoveryRuntime = browserRuntime,
): Promise<Value> => {
  try {
    return await load()
  } catch {
    if (!buildIdentity || !buildPattern.test(buildIdentity)) {
      throw new ChunkRecoveryError(true)
    }
    const attempted = attemptedBuildIdentity ?? runtime.attemptedBuild()
    if (attempted === buildIdentity) throw new ChunkRecoveryError(true)
    attemptedBuildIdentity = buildIdentity
    runtime.markAttempt(buildIdentity)
    runtime.reload()
    throw new ChunkRecoveryError(false)
  }
}

export const reloadCurrentApplication = (
  buildIdentity = currentBuildIdentity,
  runtime: ChunkRecoveryRuntime = browserRuntime,
) => {
  if (buildIdentity && buildPattern.test(buildIdentity)) {
    attemptedBuildIdentity = buildIdentity
    runtime.markAttempt(buildIdentity)
  }
  runtime.reload()
}

export const recoverableLazy = <Properties extends object>(
  load: () => Promise<{ default: ComponentType<Properties> }>,
) => lazy(() => loadRouteChunk(load))
