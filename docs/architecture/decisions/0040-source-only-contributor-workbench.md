# ADR-0040: Separate the runtime launcher from the contributor workbench

Status: Accepted

## Context

The installed command dispatcher accumulated report, catalogue, batch,
proposal, case, deterministic-decision, and contained-reviewer development
operations alongside the supported authenticated review service. Those
operations remain useful for focused evidence and qualification, but installing
them presents internal developer mechanisms as operator-facing product
contracts, expands the production parser and image, and duplicates tests around
presentation rather than shared application behavior.

The repository still needs fast, composable access to the existing application
operations without creating a plugin system, workflow language, arbitrary code
executor, second production entrypoint, or bridge to a production library.

## Decision

The installed `mechanical-librarian` command exposes only `review-serve`. The
normal container continues to launch that same runtime composition.

Move the other current development capabilities into a top-level source-only
`workbench/` package outside the production `src/` package tree. The workbench
uses a closed catalogue, typed request modules, explicit application-operation
bindings, and one execution boundary returning a bounded `WorkbenchResult` in
summary or canonical JSON form. It has no dynamic discovery, shell/Python
execution, plugins, aliases for retired commands, or generic workflow graph.

Workbench state is disposable by default. Explicit contexts admit only
synthetic fixtures, test workspaces, or approved private-corpus input; confine
paths beneath that workspace; and reject production workspaces and targets with
no force bypass. Network scenarios require a validated isolated profile with
sanitized inputs and bounded authority. Each attempt is durably recorded before
egress and binds the exact configuration/profile inputs; configured service
origins are loopback or `.invalid`, and private paths remain workspace-confined.
Fixed-origin metadata providers are refused by production composition and may be
exercised only through a focused injected fake operation.
Large outputs remain local digest-addressed artifacts under an ignored workspace, and operation
exceptions cross a redacting closed failure boundary.

The workbench reuses bootstrap composition only as an explicit source-mode
binding to the same application services used by the HTTP/runtime surfaces.
The wheel and container must not contain or import it. Exact-artifact tests prove
the installed help surface and exclusion from outside the checkout.

## Consequences

Production command parsing and packaging become smaller while contributor
evidence workflows remain reachable and testable. Presentation-coupled tests and
operator command promises for the retired surfaces are removed; domain,
application, persistence, safety, and exact-current tests remain.

Adding a workbench scenario requires a present application operation, one typed
request, explicit catalogue registration, focused proof, and documentation. It
does not establish product compatibility or runtime authority. This decision
adds no feature, provider system, production write path, migration, release,
tag, deployment, publication, or production-service use.
