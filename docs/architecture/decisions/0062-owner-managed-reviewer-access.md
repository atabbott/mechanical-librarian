# ADR-0062: Keep reviewer access owner-managed and task-scoped

- Status: accepted
- Date: 2026-08-28
- Owners: project maintainers
- Supersedes: deployment-owned reviewer enablement in ADR-0058 and the
  deferred OpenRouter boundary in ADR-0041 for `cover_observation` only
- Superseded by: none

## Context

The installed cover flow already has a closed observation packet and no write
authority, but selecting or authenticating its reviewer was deployment work.
Owners need an optional first-run and Settings path that works in both file and
store deployments without turning models into plugins, endpoints, or generic
tasks. Credentials and hosted cover processing require a narrow trust boundary.

## Decision

Application state owns one closed `cover_observation` mapping: absent, Codex,
or OpenRouter with one provider-local model ID. Deployment configuration still
owns the contained Codex profile, TLS, mounts, and process limits. The
OpenRouter key is write-only secret state; Codex authentication is staged in a
private contained `CODEX_HOME` and atomically installed only after the pinned
CLI reports the exact usable account state.

The supported image carries the exact platform build of Codex CLI 0.147.0 and
its bundled Bubblewrap artifact at fixed, read-only paths. The build admits only
`amd64` and `arm64`, verifies the selected registry tarball before extraction,
executes the pinned CLI version probe, retains artifact checksums and package
metadata, and includes the upstream Apache-2.0 licence. The deploy-owned profile
for the supported container binds those installed paths, identities, and
limits. Browser and application-owned settings cannot redirect that profile or
supply an executable.

The supported Compose runtime keeps a deny-by-default Docker seccomp profile
derived from Moby's tagged default. Its only added syscall admissions are the
two exact Bubblewrap namespace-clone masks, an exact user-namespace lock-down,
and mount-namespace construction. Network namespace creation is admitted only
as part of the exact offline version-probe mask. All capabilities remain
dropped and no-new-privileges remains active. The nested sandbox exposes no
procfs; it supplies only an immutable synthetic `/proc/self/exe` link to the
already-bound Codex binary.

Codex's own model-tool surface is closed before every observation: shell,
unified execution, agents, apps, plugins, browser/computer use, hooks, web
search, and the remaining optional tools are disabled through strict pinned-CLI
configuration. The CLI therefore uses its `danger-full-access` internal
sandbox mode inside the already-established outer Bubblewrap boundary. This
prevents an unsupported second Bubblewrap/user-namespace layer; it does not
widen the filesystem, credential, process, or network view already fixed by the
outer sandbox, and there is no model-callable process tool to inherit that view.

The supported whole-container memory ceiling is 2 GiB. The previously measured
167.8 MiB serving peak plus two possible 256 MiB preparation children reasons
to 680 MiB; one contained reviewer independently reserves 1 GiB, for a 1,704
MiB simultaneous bound. The reviewer host gate still compares its full 1 GiB
per-run contract with current available memory and cgroup headroom before every
run. The larger ceiling makes that contract reachable without treating the cap
as guaranteed free memory or weakening the live gate.

Dispatch captures the mapping, access revision, and required secret/credential
binding in one SQLite snapshot statement at the invocation boundary, constructs
exactly one selected engine, and never falls back. Same-generation owner actions
and dispatch share one application gate; independent connections serialize
writes through SQLite CAS. Observation uses only that capture and the
durable write rejects it if the access revision changed, including an A-B-A
replacement. Both
engines receive the same bounded cover packet and strict result schema.
Persisted provenance binds access revision, engine, model, schema/profile
bytes, and candidate identity. Reviewer output remains advisory and receives neither target
credentials nor write authority.

OpenRouter uses only `https://openrouter.ai/api/v1/chat/completions`, the exact
selected model, strict structured output with required parameters, bounded
responses, and no redirects or arbitrary endpoint. Ordinary Settings reads do
not probe the network; explicit refresh and dispatch use typed redacted failure
states. Model catalogue entries are evidence, not durable recommendations. No
recommendation is qualified because no live-key end-to-end call was authorized.
The UI may show the bounded delivery-time catalogue candidates and documented
free-tier caps, but labels them unqualified until the private real-cover receipt
exists. OpenRouter response streams are consumed in bounded chunks.

Codex profile parsing, staging recovery, and device-manager construction are
lazy: no-reviewer and OpenRouter operation do not touch them. Runtime generation
shutdown owns a bounded device child teardown (TERM, bounded wait, KILL, death
check, and thread join) while preserving a previously installed credential.
Starting Codex replacement first revision-advances and deselects an active Codex
mapping; successful replacement remains unselected until a fresh owner selection.
Cancellation and credential installation are one serialized terminal transition.

The new application tables, `setup-state-v2`, `owner-settings-v2`, and
`cover-consideration-v3` declare a clean rebuild under ADR-0013. Earlier
exact-predecessor plans and their public command are retired, not retargeted.

## Consequences

- First-run and Settings expose the same optional-first state grammar.
- Clearing or changing access cannot silently switch engines; an unavailable
  selected engine makes that observation unavailable.
- Public views expose secret presence and typed status only, never secret
  values, private paths, raw provider errors, or raw reviewer streams.
- The image pays the storage cost of the pinned reviewer artifacts, but no
  reviewer executable, profile, auth store, or process is touched until an
  owner explicitly uses available Codex access.
- Supporting Bubblewrap inside the outer container widens its default seccomp
  policy only by the checked-in exact namespace rules. A host that cannot
  provide unprivileged user namespaces fails Codex closed.
- OAuth, provider marketplaces, arbitrary endpoints, generic task registries,
  retries, and reviewer lifecycle management remain outside this decision.
