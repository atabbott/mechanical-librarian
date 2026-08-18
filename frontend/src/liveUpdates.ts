/**
 * One authenticated stream per tab, carrying invalidation and never state.
 *
 * A frame says which topic changed, which identity inside it changed, and where
 * in the server's total order it changed. It never says what the new value is,
 * so nothing here writes into a cache: every listener refetches over the HTTP
 * surface that was already the truth. Push decides *when* to look; HTTP remains
 * the truth of *what is there* (#641).
 *
 * The transport is `EventSource`, whose own reconnect presents `Last-Event-ID`,
 * so resume needs no machinery here. What this module owns is narrower: proving
 * a frame is a frame, refusing one that carries anything else, discarding a
 * position already seen, and reporting honestly when the stream is down so the
 * surfaces fall back to polling and say so.
 */

export const liveTopics = ['activity', 'case', 'library', 'preparation'] as const

export type LiveTopic = (typeof liveTopics)[number]
export type LiveEventName = LiveTopic | 'resync'

export interface LiveUpdate {
  readonly sequence: number
  readonly subject: string | null
  readonly topic: LiveEventName
}

/**
 * `connecting` before the first open, `live` while the server is pushing, and
 * `degraded` whenever it is not — which is the only state a surface has to show
 * and the only one that turns polling back on.
 */
export type LiveStreamState = 'connecting' | 'live' | 'degraded'

export interface LiveUpdateStream {
  readonly state: LiveStreamState
  subscribe(listener: (update: LiveUpdate) => void): () => void
  observeState(listener: (state: LiveStreamState) => void): () => void
  close(): void
}

export interface LiveUpdateStreamOptions {
  readonly path?: string
  readonly open?: (path: string) => EventSource
  readonly retryMilliseconds?: number
  readonly schedule?: (run: () => void, delay: number) => number
  readonly cancel?: (handle: number) => void
}

const caseIdPattern = /^review_case_[0-9a-f]{64}$/

/**
 * Reject anything that is not exactly the three invalidation fields.
 *
 * This is the browser half of "events contain no state". The server cannot
 * encode a payload here, and this refuses to read one if a future server tries:
 * an unknown key, a widened subject, or a non-integer position is discarded
 * whole rather than partially trusted.
 */
export const readLiveUpdate = (data: unknown): LiveUpdate | null => {
  if (typeof data !== 'string' || data.length > 512) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    return null
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null
  const value = parsed as Record<string, unknown>
  const keys = Object.keys(value)
  if (keys.length !== 3 || !keys.every((key) => ['sequence', 'subject', 'topic'].includes(key)))
    return null
  const { sequence, subject, topic } = value
  if (typeof sequence !== 'number' || !Number.isSafeInteger(sequence) || sequence < 0) return null
  if (typeof topic !== 'string' || ![...liveTopics, 'resync'].includes(topic)) return null
  if (subject !== null && (typeof subject !== 'string' || !caseIdPattern.test(subject))) return null
  return { sequence, subject, topic: topic as LiveEventName }
}

export const createLiveUpdateStream = (options: LiveUpdateStreamOptions = {}): LiveUpdateStream => {
  const path = options.path ?? '/api/v1/updates'
  const retryMilliseconds = options.retryMilliseconds ?? 5_000
  const schedule =
    options.schedule ?? ((run, delay) => globalThis.setTimeout(run, delay) as unknown as number)
  const cancel = options.cancel ?? ((handle) => globalThis.clearTimeout(handle))
  const open =
    options.open ??
    (typeof globalThis.EventSource === 'function'
      ? (target: string) => new globalThis.EventSource(target)
      : null)

  const listeners = new Set<(update: LiveUpdate) => void>()
  const stateListeners = new Set<(state: LiveStreamState) => void>()
  let source: EventSource | null = null
  let retryHandle: number | null = null
  let lastSequence = -1
  let closed = false
  let state: LiveStreamState = 'connecting'

  const setState = (next: LiveStreamState) => {
    if (state === next) return
    state = next
    for (const listener of [...stateListeners]) listener(next)
  }

  const deliver = (raw: unknown) => {
    const update = readLiveUpdate(raw)
    if (update === null) return
    if (update.topic === 'resync') {
      // A resync is the server saying it cannot answer this reader's position
      // exactly -- a first connect, a gap past the retained ring, or a restarted
      // process whose order begins again. The position is adopted rather than
      // compared, which is what stops a restart's low sequences being read as
      // duplicates and silently dropped.
      lastSequence = update.sequence
    } else {
      if (update.sequence <= lastSequence) return
      lastSequence = update.sequence
    }
    for (const listener of [...listeners]) listener(update)
  }

  const connect = () => {
    if (closed || open === null) {
      if (open === null) setState('degraded')
      return
    }
    const stream = open(path)
    source = stream
    stream.onopen = () => setState('live')
    stream.onerror = () => {
      setState('degraded')
      // `EventSource` reconnects on its own while the connection merely broke.
      // A closed one is a refusal -- an expired session, a build the server no
      // longer serves -- and only a fresh attempt can resolve it, so this is the
      // one case that needs a retry of its own.
      if (stream.readyState !== 2 || closed) return
      stream.close()
      source = null
      retryHandle = schedule(() => {
        retryHandle = null
        connect()
      }, retryMilliseconds)
    }
    for (const name of [...liveTopics, 'resync'] as const) {
      stream.addEventListener(name, (event) => {
        deliver(event.data)
      })
    }
  }

  connect()

  return {
    get state() {
      return state
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    observeState(listener) {
      stateListeners.add(listener)
      return () => stateListeners.delete(listener)
    },
    close() {
      closed = true
      if (retryHandle !== null) cancel(retryHandle)
      retryHandle = null
      source?.close()
      source = null
      listeners.clear()
      stateListeners.clear()
    },
  }
}
