# Live title proposal report

`live-title-proposal-report` joins one exact current Audiobookshelf ebook
snapshot and its current durable EPUB opening evidence to one current configured
Hardcover title/identity observation. It never contacts either service: prepare
the F07b and F09f evidence first.

```console
mechanical-librarian live-title-proposal-report \
  --config /config/config.json \
  --database /data/mechanical-librarian.sqlite \
  --target target_0123456789abcdef0123456789abcdef \
  --snapshot snapshot_0123456789abcdef0123456789abcdef \
  --file-index 0 \
  --format json
```

The selected configured ebook mount must be read-only. Exactly one current
local title and one distinct canonical edition-applicable ebook ISBN must be
usable, and the current Hardcover observation must contain the matching
compatible edition and an agreeing title. A loaded Audiobookshelf title that is
empty or materially different produces one immutable `needs_review`,
`production_report_only` proposal whose value is the exact normalized local
title.

All other valid executions persist and report no proposal using one stable
category: `source_not_ready`, `provider_not_ready`, `evidence_disagreement`, or
`no_change`. Identical current inputs replay the existing set; changed inputs
create an immutable successor. Final source and provider evidence is revalidated
inside the transaction that inserts history and advances the current pointer.

Both output formats omit ISBNs, provider identifiers, private work keys, source
fingerprints, and configuration digests. Exit `0` means a canonical report was
published, including a zero-proposal report. Invalid invocation exits `2` with
one fixed diagnostic; runtime or state failure exits `1`. The command admits no
review case, makes no decision, and has no approval, apply, target-write, or
media-write authority.
