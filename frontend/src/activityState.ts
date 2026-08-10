import type { ActivityView } from './api/http'

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const primitive = (value: unknown) => value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? value : null
const identity = (event: Record<string, unknown>) => JSON.stringify([primitive(event.work_id), primitive(event.destination), primitive(event.state), primitive(event.occurred_at) ?? primitive(event.recorded_at), primitive(event.library), primitive(event.origin), primitive(event.failure)])

export const activityEventKeys = (events: readonly Record<string, unknown>[]) => {
  const occurrences = new Map<string, number>()
  const keys = new Array<string>(events.length)
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (!event) continue
    const value = identity(event)
    const ordinal = occurrences.get(value) ?? 0
    occurrences.set(value, ordinal + 1)
    keys[index] = `${value}:${String(ordinal)}`
  }
  return keys
}

export const activityOutcome = (data: ActivityView | undefined, command: { path: string; body?: unknown }) => {
  if (!data) return 'unknown'
  if (command.path === 'field-effects/pause') return data.paused === true ? 'applied' : 'unknown'
  if (command.path === 'field-effects/resume') return data.paused === false ? 'applied' : 'unknown'
  if (command.path.endsWith('/revoke')) {
    const [, connection, library] = /^activity\/([^/]+)\/([^/]+)\/revoke$/.exec(command.path) ?? []
    if (!connection || !library || !Array.isArray(data.enabled_libraries) || !data.enabled_libraries.every((item) => { const value = item as Record<string, unknown>; return record(item) && typeof value.connection === 'string' && typeof value.library === 'string' })) return 'unknown'
    return data.enabled_libraries.some((item) => { const value = item as Record<string, unknown>; return value.connection === decodeURIComponent(connection) && value.library === decodeURIComponent(library) }) ? 'unknown' : 'applied'
  }
  if (command.path !== 'activity/consent' || !record(command.body) || !Array.isArray(command.body.grants) || command.body.grants.length === 0 || !Array.isArray(data.capabilities)) return 'unknown'
  for (const grant of command.body.grants) {
    if (!record(grant) || typeof grant.library !== 'string' || !Array.isArray(grant.destinations) || grant.destinations.length === 0 || !grant.destinations.every((destination) => typeof destination === 'string')) return 'unknown'
    const capabilities = (data.capabilities as unknown[]).filter((item) => record(item) && item.library === grant.library)
    const capability = capabilities[0]
    if (capabilities.length !== 1 || !record(capability) || !Array.isArray(capability.destinations)) return 'unknown'
    for (const requested of grant.destinations) {
      const matches = capability.destinations.filter((destination) => record(destination) && destination.destination === requested)
      const match = matches[0] as Record<string, unknown> | undefined
      if (matches.length !== 1 || typeof match?.consented !== 'boolean') return 'unknown'
      if (!match.consented) return 'unknown'
    }
  }
  return 'applied'
}
