# Foreground evidence batches

`batch preview` freezes an explicit ordered selection or current eligible
catalogue entries in an operator-supplied, absent manifest path. The manifest is
canonical, content-addressed, bounded, regular-file-only, and contains no
titles, authors, paths, credentials, reports, or execution history.

`batch inspect` reads only that manifest. `batch run` revalidates its
configuration and current catalogue snapshot before exercising serial report
generation. Source drift and ineligible entries are reported as item outcomes;
configuration, catalogue, credential, or unavailable authority fails safely.
There is deliberately no queue, worker, retry, resume, lock, or background
state. Recovery is a fresh operator-created manifest.

The manifest directory must be private to the operator. Creation uses exclusive
no-follow open; read validates a regular no-follow descriptor, strict UTF-8,
canonical bytes, schema, bounds, and its content-addressed identifier before
any report authority is constructed.
