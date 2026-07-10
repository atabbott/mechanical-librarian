# Security Policy

Mechanical Librarian processes untrusted metadata, archives, images, filenames, web
responses, and AI output while holding credentials capable of changing a media
library. Security is a core product feature.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. The private reporting
channel will be added before repository publication. Until then, report issues
directly to the project owner.

Include:

- affected version or commit;
- configuration and deployment shape;
- reproduction steps using synthetic data;
- expected and observed behavior;
- potential impact;
- suggested mitigation, if known.

Do not include real credentials, private media, or personal database snapshots.

## Supported versions

No supported public version exists yet. Before the first stable release this
section must define the supported release window and security backport policy.

## Security promises

- AI processes cannot access library-service credentials or invoke production
  writes.
- Media mounts are read-only by default.
- Normal writes use supported service APIs.
- Destructive work requires explicit human authorization.
- Secrets are redacted from logs, reports, errors, exports, and diagnostics.
- Every apply operation has pre-state evidence and post-write verification.
- Untrusted archives, images, HTML, paths, URLs, and provider responses are
  bounded and validated before use.

The complete threat model and required mitigations are in
[docs/architecture/security.md](docs/architecture/security.md).
