# Current book catalogue

`mechanical-librarian catalogue refresh --config CONFIG --database CATALOGUE`
creates one bounded, read-only observation across configured Audiobookshelf book
libraries. The command accepts only `config-v5`; it reads no provider credential
and makes no target or media write.

The catalogue stores no more than 10,000 entries. A current generation is
published by one SQLite transaction after summary/detail `updatedAt` validation.
The preceding generation remains readable until that switch. A second refresh
against the same catalogue database returns busy; it does not wait or retry.
Later service drift is outside the generation's bounded observation window.

`catalogue list` emits deterministic JSON rows and accepts `--filter` with
`eligible`, `ineligible`, `ebook`, or `audio`. `catalogue get --connection NAME
--library NAME --library-id LIBRARY_ID --item ID` emits one exact stored entry. List output never
contains a path. Exact lookup can include the service-relative EPUB path; command
errors remain value-free and logs must redact titles, authors, identities, paths,
and credentials.

The database retains only the installed `catalogue-v1` schema. A different or
tampered schema fails closed and requires an operator-created clean database.
Configuration changes produce `catalogue requires refresh`; they never silently
adapt a current generation.

The current generation can be frozen into an explicit or all-eligible
foreground evidence batch. See
[Foreground ebook evidence batches](ebook-evidence-batches.md).
