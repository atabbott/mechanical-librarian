# Mechanical Librarian

Mechanical Librarian is a self-hosted, evidence-first metadata curator for
ebook and audiobook libraries. The project is pre-release; there is no
production image, supported release, or write-capable runtime yet.

## Current checkpoint

The current 0.10 development checkpoint installs a bounded, evidence-first ebook
evidence workflow. An operator can:

- refresh and inspect a current catalogue across configured Audiobookshelf book
  libraries;
- create and inspect an immutable manifest for explicit selections or all
  eligible EPUBs;
- run that frozen batch serially in the foreground;
- generate or reopen one complete source-attributed EPUB metadata-and-cover
  evidence report; and
- create or reopen one immutable fixed-policy ProposalSet from an exact current
  report; and
- admit one exact current actionable ProposalSet as an immutable undecided
  review case, with typed no-case retirement for non-actionable sets; and
- preview, request, inspect, or explicitly reevaluate one isolated
  qualification-only Codex shadow recommendation over an exact current case;
  and
- compute one canonical provider-independent deterministic decision preview for
  an exact current case without model/provider calls, persistence, or write
  authority.

The report combines all 16 current Audiobookshelf metadata destinations,
bounded structured EPUB evidence, independent configured Hardcover and Open
Library observations, and validated cover artifacts. Every distinct
checksum-valid ISBN-10/13 family from bounded package, front, or rear evidence
gets its own exact discovery lane, while one complete title-plus-author work
lane runs alongside it when available. Same-provider lane convergence remains
one correlated source, conflicts stay unresolved, and work evidence never
becomes edition evidence. The provider gallery retains at
most eight sources and four per provider with safe provenance and explicit
truncation. The report remains neutral. A separate deterministic evaluator
produces exactly one explainable result for each destination without contacting
providers, configurable ranking, or write authority. Catalogue and batch
operations retain the same read-only boundary. Review admission records no
decision, apply, target write, media write, background execution, automatic
retry, or supported release. Shadow advice is permanently
`qualification_only`, uses an exact pinned tuple under a Linux sandbox, and
adds no normal review activation or authority.

Only absent or exact-current pre-release state is supported. Earlier
development databases, manifests, configuration schemas, and retired commands
are intentionally rejected rather than migrated or interpreted.

The installed command surface contains only the authenticated `review-serve`
launcher. Contributor-only catalogue, evidence, proposal, case, decision, and
contained-reviewer workflows live in a typed source-only workbench that is
excluded from the wheel and container and refuses production targets.

See the [catalogue guide](docs/operations/catalogue.md),
[foreground batch guide](docs/operations/ebook-evidence-batches.md),
[metadata report guide](docs/operations/metadata-report.md),
[ProposalSet guide](docs/operations/proposal-set.md), [review-case guide](docs/operations/review-case.md),
[shadow-review guide](docs/operations/shadow-review.md), and
[documentation index](docs/README.md).

## Product direction

The intended product remains one self-contained container beside a media
library, with Audiobookshelf as the first adapter, supported service APIs for
future metadata writes, read-only media mounts by default, explicit safety
gates, and durable auditability. AI reviewers remain untrusted and never
receive service credentials or production write authority.

Do not use real library databases, copyrighted book contents, personal
domains, tokens, or unredacted reports in issues, tests, or pull requests.

## Contributing and licence

Development and review expectations are in [CONTRIBUTING.md](CONTRIBUTING.md).
Mechanical Librarian is licensed under [AGPL-3.0-or-later](LICENSE).
