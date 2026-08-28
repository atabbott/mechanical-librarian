# Mechanical Librarian

Mechanical Librarian is a self-hosted, evidence-first metadata curator for
ebook and audiobook libraries. The project is pre-release; there is no
published production image or supported release. Target writes remain disabled
until an owner explicitly grants library-scoped consent and every guarded
effect gate passes.

## Current checkpoint

The 0.17.0 configuration and first-run checkpoint runs one authenticated owner
workspace. A first visitor creates the owner password, connects Audiobookshelf,
selects book libraries and optional metadata providers, verifies read-only
mounts, optionally configures one cover reviewer, and starts preparation
without restarting the container. Settings is a
primary workspace destination, and store-managed changes take effect as soon as
they are saved; deploy-managed TLS, listener, proxy, and
mount values remain outside browser authority. The workspace prepares exact-current
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

Ordinary startup supports only absent or exact-current pre-release state. This
build retires the previous offline schema-adoption plans and command; every
earlier database, manifest, configuration schema, or retired command is
rejected rather than interpreted and the application state needs a clean
rebuild.

The owner may continue without a reviewer or select exactly one engine for
`cover_observation`: contained Codex device sign-in or an owner-supplied
write-only OpenRouter key and exact model ID. Reviews remain advisory and never
receive target credentials or write authority. OpenRouter sends bounded cover
previews to its fixed API origin and the selected provider; owners should check
current tier, capability, and data-use terms. Catalogue-capable free and paid
candidates are labelled unqualified; no model recommendation is qualified by
delivered real-cover evidence in this build.

The installed command surface contains the authenticated `review-serve`
workspace, single-policy `policy-preview`, owner-directed `cover-review`, and
read-only guarded-history `review-activity` commands. The generic contributor
workbench remains excluded from the wheel and container; the installed cover
flow uses only its bounded observer contract and deterministic policy.
The authenticated Library, Attention, review-detail, Policy, and Activity URLs
share one persistent packaged React workspace; FastAPI retains every session,
currentness, policy, effect, persistence, and mutation decision, and `/login`
remains server-rendered.

Library keeps every current work discoverable while Attention leads with fields
that benefit from an owner decision. Review detail records the selected current
or specific proposed value immediately, without generic outcome controls or a
separate submit step. Policy is a semantic notebook, and Activity is the calm
consent, guarded-work, history, and recovery ledger. The five surfaces share one
responsive editorial language at desktop and compact widths in light and dark
themes. See the [0.14.1 interface-fidelity reconciliation](docs/development/0-14-1-interface-fidelity.md)
for the protected-reference boundary and cumulative qualification contract.

See the [catalogue guide](docs/operations/catalogue.md),
[foreground batch guide](docs/operations/ebook-evidence-batches.md),
[metadata report guide](docs/operations/metadata-report.md),
[ProposalSet guide](docs/operations/proposal-set.md), [review-case guide](docs/operations/review-case.md),
[contributor workbench](docs/development/contributor-workbench.md), and
[documentation index](docs/README.md).

The active authenticated-browser source is maintained under `frontend/`. Its
exact source checks require Node 24.19.0 and npm 11.17.0: run
`npm ci --ignore-scripts`, then `npm run typecheck`, `npm run lint`, `npm test`,
and `npm run build` from that directory. Each of those four commands
regenerates the DTOs first, so `npm run generate` is needed only when you want
the generated types on their own. `npm run lint` also checks Prettier
formatting for `frontend/src`; `npm run format` rewrites it. Generated DTOs,
compiled assets, source maps, `node_modules`, and Node itself do not belong in
source or the runtime container.

## Product direction

One self-contained container beside an existing library; Audiobookshelf first;
supported service APIs only, never a direct database edit; read-only media
mounts; AI reviewers with no credential or write authority; no unattended
destructive work. [PRODUCT.md](PRODUCT.md) is the single product-boundary
statement, and [scope](docs/product/scope.md) records what the first release
admits and refuses.

Do not use real library databases, copyrighted book contents, personal
domains, tokens, or unredacted reports in issues, tests, or pull requests.

## Contributing and licence

Development and review expectations are in [CONTRIBUTING.md](CONTRIBUTING.md).
Mechanical Librarian is licensed under [AGPL-3.0-or-later](LICENSE).
