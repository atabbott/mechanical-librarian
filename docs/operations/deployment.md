# Deployment model

The 0.10 development runtime is an unreleased, explicitly invoked CLI package.
It supports current catalogue, report, ProposalSet, ReviewCase, and
qualification-only shadow-review operations; it is not a daemon or published
container release.

## Runtime prerequisites

Run as a non-root dedicated identity with an explicit `config-v4` file,
exact-current SQLite application and catalogue databases on reliable local
storage, an owner-controlled artifact store, and—when using batches—an immutable
manifest path. Target and enabled-provider secrets must remain owner-only, and
ebook media mounts are read-only. The configuration and supported egress rules
are defined in the [configuration reference](../reference/configuration.md) and
[security model](../architecture/security.md).

Qualification-only reviewer commands additionally require an owner-only
qualification profile, private `0700` auth-store, empty private job parent, and
the exact maintainer-provisioned Codex CLI and Bubblewrap artifacts declared by
that profile. The runtime neither installs, upgrades, nor bundles those
artifacts; their profile, authentication, and isolation requirements are
authoritative in the [shadow-review guide](shadow-review.md). Authentication and
shadow advice do not activate normal review or grant decision, apply, or write
authority.

The process needs no HTTP port, queue, scheduler, worker, external database, or
writable media mount. Do not mount the Docker socket or grant host networking,
elevated capabilities, or write access to media. Keep the root filesystem
read-only where packaging permits.

## Backup and recovery

Back up the complete application SQLite file using a SQLite-supported
consistent method, and keep configuration, qualification profiles, executable
paths, and auth stores in the host's encrypted secret-backup policy. Restore
only a verified complete copy or start clean exact-current state; never edit
rows or pointers. The full procedure and retained-data posture are in
[backups and recovery](backups-and-recovery.md).

The runtime has no target or media write authority. Future container release,
dashboard, worker, normal reviewer activation, and write-capable deployment
remain outside the installed 0.10 authority.
