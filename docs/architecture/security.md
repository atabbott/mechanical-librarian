# Security and Threat Model

## Security objectives

1. Protect service credentials, AI credentials, private media, and personal
   library data.
2. Prevent untrusted inputs or AI output from gaining production authority.
3. Make every production change bounded, attributable, reversible where
   possible, and independently verified.
4. Fail closed for writes while continuing safe inventory and diagnosis.
5. Ship secure defaults that do not require expert container hardening.

## Trust boundaries

### Trusted with production authority

- Curator application process.
- Validated service adapter write methods.
- Migration and backup code running within its declared scope.

### Trusted for reads but not writes

- Bounded media extractors.
- Provider adapters after transport and schema validation.
- Inventory and reconciliation jobs.

### Untrusted

- Ebook and audiobook file contents.
- Filenames, folder names, embedded tags, HTML, and images.
- Provider responses and cover URLs.
- Library-service event payloads until re-read through the API.
- AI prompts, reasoning, tool requests, and output.
- Browser input and uploaded files.
- Dependencies, container base images, and update artifacts until verified.

## Principal separation

The single container must preserve least privilege internally:

- `curator` can read configured media and service secrets.
- `reviewer` can access only staged evidence, explicitly attached images, its
  own authentication state, and its output path.
- The reviewer has no service secret, main database, backup, production media,
  container-control, or apply access.
- The supervisor starts principals but does not expose a general privileged
  control socket.

This boundary must be tested from inside the published image.

## Primary threats and mitigations

### Prompt injection in books or provider metadata

Risk: embedded text instructs the AI to read secrets, execute tools, ignore
policy, or invent approval.

Mitigations:

- Treat extracted text as quoted evidence, never instructions.
- Use an evidence schema that separates facts from system policy.
- Disable or deny reviewer command execution where possible.
- Remove production secrets and paths from the reviewer namespace.
- Validate output against a closed JSON schema and supplied proposal IDs.
- Reject decisions citing unknown evidence or proposing undeclared fields.

### Credential disclosure

Risk: service or AI credentials enter logs, diagnostics, reports, subprocess
environments, crash dumps, or the dashboard.

Mitigations:

- Load credentials through a secret abstraction with file-secret support.
- Pass a minimal environment to subprocesses.
- Use separate Unix principals and deny cross-principal reads.
- Centralize redaction and test known token, URL, header, and query patterns.
- Never include raw request headers or environment dumps in support bundles.

### Server-side request forgery

Risk: malicious cover or provider URLs target localhost, private networks,
cloud metadata services, or unsupported protocols.

Mitigations:

- Permit only HTTPS by default.
- Resolve and reject loopback, link-local, multicast, private, and reserved
  destinations unless an adapter explicitly owns the local service.
- Re-check every redirect destination.
- Bound redirects, response size, content type, and timeout.
- Keep provider hosts in adapter-specific allow rules where practical.

### Archive, image, and parser attacks

Risk: zip bombs, path traversal, decompression bombs, malformed tags, parser
vulnerabilities, excessive memory, or long-running extraction.

Mitigations:

- Never extract archives by trusting stored paths.
- Bound entry count, compressed and expanded bytes, nesting, text, and time.
- Decode images with pixel and byte limits.
- Run external parsers without shell interpolation, with time and resource
  limits, and with read-only input.
- Bound audio decode, sample duration, temporary output, transcription time,
  model resources, and concurrency; delete transient audio on every terminal
  and recovery path.
- Keep parser dependencies updated and covered by malformed-input tests.

### Stale or conflicting writes

Risk: a user, scan, import, or another curator job changes an item after review.

Mitigations:

- Guard every apply with the expected fingerprint.
- Serialize writes per service and item.
- Suppress or reconcile self-generated update events.
- Supersede stale plans instead of force-applying them.

### Supply-chain compromise

Risk: malicious dependency, base image, build action, or release artifact.

Mitigations:

- Pin dependencies and base images by reviewed versions or digests.
- Generate an SBOM for releases.
- Scan dependencies, code, images, and secrets in CI.
- Sign release images and publish checksums and provenance.
- Minimize runtime packages and remove build tools from the final image.

### Dashboard compromise

Risk: unauthorized user approves work, reads private metadata, obtains tokens,
or invokes administrative functions.

Mitigations:

- Require authentication on non-health routes.
- Use secure session cookies, CSRF defenses, rate limits, and origin checks.
- Never return credentials to the browser after initial submission.
- Separate approval, configuration, backup, and write-enablement permissions
  even if the initial product has one owner.
- Render metadata as text or sanitized HTML under a restrictive CSP.

## Write-safety gates

Production writes require all of:

- compatible adapter capability;
- healthy service connection;
- stable current fingerprint;
- complete decision provenance;
- strict plan validation;
- verified backup readiness;
- service and item locks;
- active circuit breaker;
- allowed policy disposition;
- post-write read-back verification.

Missing any gate produces a visible blocked state. Emergency overrides are out
of scope for unattended operation and must never be hidden configuration.

## Security testing requirements

- Secret-redaction golden tests.
- Prompt-injection and hostile-metadata tests.
- SSRF and redirect tests.
- Zip, image, path traversal, and parser resource-bound tests.
- Container tests proving reviewer isolation.
- Authorization and CSRF integration tests.
- Apply concurrency, stale fingerprint, replay, and rollback tests.
- Dependency, image, static-analysis, and secret scans on every release.

## Open issues before public release

- Select the dashboard authentication mechanism.
- Select and prove the internal reviewer sandbox implementation.
- Define signed-update and rollback behavior.
- Define private vulnerability reporting contact and response targets.
- Complete a third-party-oriented container hardening review.
