# Security Policy

Mechanical Librarian processes untrusted metadata, archives, images, filenames, web
responses, and AI output while holding credentials capable of changing a media
library. Security is a core product feature.

## Reporting a vulnerability

Do not open a public issue, discussion, or pull request for a suspected
vulnerability, and do not disclose it anywhere public before a fix is
available.

Report it privately through GitHub private vulnerability reporting: open the
repository's **Security** tab and choose **Report a vulnerability**, or go
directly to
<https://github.com/atabbott/mechanical-librarian/security/advisories/new>.
The report and its follow-up stay private until the maintainers publish an
advisory.

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
