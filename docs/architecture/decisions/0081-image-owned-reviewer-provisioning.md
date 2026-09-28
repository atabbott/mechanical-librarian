# ADR-0081: Ship and validate the contained reviewer profile

- Status: accepted
- Date: 2026-09-28
- Owners: project maintainers
- Supersedes: none (extends ADR-0062's profile authorship and passive-read boundary)
- Superseded by: none

## Context

The supported image already installs pinned Codex and Bubblewrap artifacts.
Requiring an owner-authored companion profile made ordinary setup unavailable,
and confused missing provisioning with unsupported host capabilities. The owner
approved image-generated provisioning with an explicit deployment override on
2026-09-21 (#1009). Browser exclusion remains the reason for deployment authority;
manually authoring values already determined by the image is unnecessary.

## Decision

The image authors canonical `contained-reviewer-profile-v1` bytes from its
verified platform artifacts and metadata at
`/opt/mechanical-librarian/reviewer/profile.json`. The file is owned by UID 65532,
mode 0400, on the read-only image filesystem. Store-mode setup and normal runtime
use it when bootstrap omits `cover_reviewer_profile_file`. An explicit bootstrap
path wins, including when it is invalid. File-mode configuration retains explicit
deployment authority and has no implicit profile fallback.

The model remains `gpt-5.6-sol`, reasoning `medium`, timeout 180 seconds, output
65,536 bytes, file 1,048,576 bytes, memory 1 GiB and process limit 32. The image
entrypoint initializes only `/data/reviewer`, its `auth` directory and its `jobs`
directory as private owner directories. Unsafe existing entries are refused;
there is no arbitrary-path repair, runtime regeneration or profile migration.

Runtime composition passively reads bounded canonical profile bytes and validates
the required model, memory and process contract. It does not inspect executables,
authenticate, start a process or use the network. Generic views reuse that passive
evidence. Explicit reviewer actions and passive evidence-currentness consumers
re-admit current bytes; invocation binds the admitted bytes rather than reopening
a different profile. Readiness precedence is provisioning, host, credential,
without overriding ordinary session, origin or revision gates. Cancellation and
credential clearing remain recovery actions during profile drift.

Provisioning has distinct missing, unreadable and contract-mismatch reasons.
Neither authentication nor raising host memory repairs those deployment facts.
The browser can select optional tasks, authenticate and test existing access;
it cannot edit profiles, executable paths, mounts, limits or engine endpoints.
Both tasks remain advisory-only. Policy preferences and Activity write consent
remain separate authorities. First-run chooses covers; optional descriptions
are configured in Settings after setup.

The new image profile has operational and synthetic evidence only. Historical
39-invocation/111-observation evidence binds its historical complete profile;
matching model/resource expressions does not transfer that qualification to new
architecture-specific executable identities. This decision changes no model,
prompt, tool surface, provider routing or data-use policy and authorizes no new
hosted measurement or spend. Measured pricing work remains #1069.

## Consequences

Ordinary supported-container setup no longer needs a companion profile. An
explicit deployment override remains authoritative and fails closed. The prior
ADR-0062 lazy-profile statement is extended only for passive contract admission;
authentication, staging and process construction remain lazy. No persisted
schema changes: `reviewer-access-v2` remains; the transient view becomes
`reviewer-access-view-v2`. ADR-0013 therefore requires no rebuild for this view.
Historical qualification decoding does not grant current automatic authority.
