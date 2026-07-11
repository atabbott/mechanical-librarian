# Mechanical Librarian

> The project is pre-release and the repository remains private during initial
> development.

Mechanical Librarian is a self-hosted service that keeps ebook and audiobook
metadata, covers, series information, and identifiers correct with minimal
human intervention. It is designed to run continuously beside a media library,
make evidence-backed decisions, apply safe changes through supported service
APIs, and retain a complete audit history.

The first production integration targets Audiobookshelf for both ebooks and
audiobooks. The architecture keeps library services, metadata providers, and AI
reviewers behind adapters so additional integrations can be added without
changing the domain model.

## Product goals

- New books are normally correct without the owner noticing.
- Human review is reserved for genuinely unresolved or destructive decisions.
- Every automatic decision and production change is explainable and auditable.
- One container, one persistent data volume, optional read-only library mounts,
  and one HTTP port are sufficient for the normal deployment.
- AI is an untrusted decision-support component and never has production write
  authority.
- Safe defaults, backups, validation, verification, and rollback are built in.
- The project is portable, documented, testable, and suitable for public
  contribution from its first release.

## Current status

The project is in its initial implementation stage. An installable typed Python
package, architecture-boundary scaffold, and supported durable-state diagnostic
exist, but no production image or supported release exists yet. See the
[durable state check](docs/operations/state-check.md) for the current executable
operator surface.

The earlier Ebook Management workspace is a prototype and evidence source. Its
completed DUX dashboard work is frozen as a traceable baseline and informs the
production [review experience](docs/product/review-experience.md), but it is not
a runtime dependency. Components may be extracted only after they are made
portable, given narrow interfaces, and covered by isolated tests.

## Documentation

Start with the [documentation index](docs/README.md).

- [Product vision](docs/product/vision.md)
- [Scope and non-goals](docs/product/scope.md)
- [Automation policy](docs/product/automation-policy.md)
- [Bounded EPUB package and opening-matter evidence](docs/product/opening-ebook-evidence.md)
- [Bounded opening-audio evidence](docs/product/opening-audio-evidence.md)
- [Review experience](docs/product/review-experience.md)
- [Initial build plan](docs/product/initial-build-plan.md)
- [Architecture overview](docs/architecture/overview.md)
- [Security model](docs/architecture/security.md)
- [Data privacy](docs/architecture/data-privacy.md)
- [Quality strategy](docs/development/quality-strategy.md)
- [DUX extraction record](docs/development/dux-extraction.md)
- [Roadmap](docs/product/roadmap.md)
- [Architecture decisions](docs/architecture/decisions/README.md)

## Contributing

The project is not ready for external contributions until its publication
readiness gates pass. The intended contribution and review model is documented
in [CONTRIBUTING.md](CONTRIBUTING.md).

Questions and diagnostic reports will follow the public [support policy](SUPPORT.md).

Do not use real library databases, copyrighted book contents, personal domains,
tokens, or unredacted production reports in issues, tests, or pull requests.

## Licence

Mechanical Librarian is licensed under
[AGPL-3.0-or-later](LICENSE). The rationale and project-material policy are in
[the licence decision record](docs/product/license-decision.md).
