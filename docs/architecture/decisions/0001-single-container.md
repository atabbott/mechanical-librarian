# ADR-0001: Ship One Self-Contained Container

- Status: accepted
- Date: 2026-07-10
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The target users are self-hosters running Docker-capable NAS or Linux systems.
Requiring a database, queue, scheduler, AI sidecar, and web service as separate
containers would increase installation, support, backup, networking, and
upgrade complexity beyond the needs of a personal library.

Security still requires separation between the production-authorized curator
and the AI reviewer.

## Decision

The normal deployment is one container, one persistent data volume, optional
read-only media mounts, and one HTTP port.

The image may supervise multiple internal principals and transient processes.
Internal process count is an implementation detail; deployment count is one.
SQLite, the scheduler, job executor, dashboard assets, evidence tools, and AI
runner ship in the image.

External databases or queues may be added later as optional advanced features,
not normal prerequisites.

## Consequences

### Positive

- Simple installation, backup, upgrade, and support model.
- Fits TrueNAS, Unraid, Synology, Portainer, and ordinary Docker.
- No coordination of companion-service versions.
- One persistent volume contains all product-owned state.

### Negative

- UI and background processing share a failure domain.
- The image is larger and internal supervision is more complex.
- Horizontal scaling is not a first-release goal.
- Internal privilege separation and migration behavior require strong tests.

### Neutral or deferred

- Reverse proxies, tunnels, and external identity remain optional user
  infrastructure.

## Alternatives considered

### Multi-container application stack

Rejected as the default because operational complexity outweighs scaling and
isolation benefits at the intended workload.

### Hosted control plane with local agent

Deferred because it creates cost, dependency, and network complexity without
being necessary for continuous NAS operation.

## Validation

- A new user can install through one `docker run` or one-service Compose file.
- Container tests prove reviewer isolation and restart recovery.
- Backup and restore require only documented external service backups plus the
  one product data volume.
