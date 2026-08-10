# Mechanical Librarian

Mechanical Librarian is a self-hosted, evidence-first metadata curator for
ebook and audiobook libraries. The project is pre-release; there is no
published production image or supported release. Target writes remain disabled
until an owner explicitly grants library-scoped consent and every guarded
effect gate passes.

## Current checkpoint

The 0.14.0 release-candidate checkpoint runs one authenticated owner workspace.
It discovers configured Audiobookshelf ebook libraries, prepares exact-current
reports, ProposalSets, and review cases in the background, presents Library,
Attention, detail, Policy, and Activity through one packaged React shell, and
records immutable human decisions. Qualified policy categories may produce
deterministic decision-only automatic outcomes without model dispatch. Target
effects require explicit consent, backup readiness, field-local exact-current
plan validation, supported-service API writes, read-back verification, and
durable audit/recovery state. Audiobookshelf has no target-side compare-and-swap:
a same-field update can be lost between the final authoritative pre-read and the
write request, so read-back reconciliation handles refusal, ambiguity, drift,
or disagreement rather than promising generic optimistic concurrency. Media
mounts remain read-only.

Contributor-only catalogue, batch, evidence, proposal, case, deterministic
preview, and contained-reviewer scenarios remain available through the typed
source workbench against disposable or explicitly approved private test state.
They are not installed runtime commands and cannot bridge to production targets.

Only absent or exact-current pre-release state is supported. Earlier
development databases, manifests, configuration schemas, and retired commands
are intentionally rejected rather than migrated or interpreted.

The installed command surface contains only the authenticated `review-serve`
launcher. The source-only workbench is excluded from the wheel and container.
The authenticated Library, Attention, review-detail, Policy, and Activity URLs
share one persistent packaged React workspace; FastAPI retains every session,
currentness, policy, effect, persistence, and mutation decision, and `/login`
remains server-rendered.

See the [catalogue guide](docs/operations/catalogue.md),
[foreground batch guide](docs/operations/ebook-evidence-batches.md),
[metadata report guide](docs/operations/metadata-report.md),
[ProposalSet guide](docs/operations/proposal-set.md), [review-case guide](docs/operations/review-case.md),
[contributor workbench](docs/development/contributor-workbench.md), and
[documentation index](docs/README.md).

The active authenticated-browser source is maintained under `frontend/`. Its
exact source checks require Node 24.19.0 and npm 11.17.0: run
`npm ci --ignore-scripts`, `npm run generate`, `npm run typecheck`, `npm run
lint`, `npm test`, and `npm run build` from that directory. Generated DTOs,
compiled assets, source maps, `node_modules`, and Node itself do not belong in
source or the runtime container.

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
