# Application contracts

The 0.4 application service owns only the contracts required by its current
caller:

- `TargetReader.read(item)` returns one bounded current target snapshot;
- `EpubInspector.inspect(...)` returns bounded local claims and a content
  digest, while `unchanged(...)` performs the final local-media check;
- `MetadataProvider.observe(claims)` performs one concrete provider
  observation; and
- `ReportRepository.publish/get/close` transactionally persists and validates
  whole reports.

Adapters implement these contracts with typed application/domain values.
Vendor payloads, HTTP clients, SQLite rows, paths, and credential bytes do not
cross into the domain. Bootstrap owns explicit concrete composition and lazy
credential-bearing provider construction.

There is deliberately no generic provider registry, pagination port, job or
retry contract, review/apply port, write grant, cache, background executor, or
future format dispatch surface in 0.4.
