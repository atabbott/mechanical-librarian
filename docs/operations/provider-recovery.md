# Provider recovery

Terminal provider failures are recorded locally without retrying a provider.
Transient and rate-limited failures wait for either a strict 1–86,400 second
`Retry-After` delay or 2, 4, 8, 16, then 24 hours. Authentication,
authorization, and configuration failures are dormant.

After correcting a dormant condition, increment the positive
`provider_recovery_generation` in the `config-v6` file and restart the
container. No environment variable, file watcher, configuration hash, or
manual retry endpoint wakes it. This increment only makes one bounded canary
eligible for future activation; this release intentionally does not execute a
provider call, replay work, fan out to siblings, or write target metadata.

Recovery state deliberately contains only bounded connection, adapter, scope,
generation, state, attempt, and timing data. It never stores credentials,
provider payloads, raw retry headers, query values, filesystem paths, or work
identifiers.
