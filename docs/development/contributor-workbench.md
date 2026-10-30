# Contributor workbench

The `workbench/` package drives the same application operations as the
product against disposable or explicitly approved private test state. It is
repository-only: it has no installed entry point and is excluded from the
wheel and the container. The installed `mechanical-librarian` command is
described in the [deployment guide](../operations/deployment.md). The design is
[ADR-0040](../architecture/decisions/0040-source-only-contributor-workbench.md).

List the catalogue from a checkout:

```text
PYTHONPATH=src:workbench uv run python -m mechanical_librarian_workbench --format summary
```

`--format json` prints the same catalogue as JSON. The command only lists
entries; scenarios run through the typed Python API.

## Catalogue

The catalogue is closed. Each entry is a typed request bound to one or more
application operations:

| Entries | Purpose |
| --- | --- |
| `catalogue-refresh`, `catalogue-list`, `catalogue-get` | Build and read a test catalogue ([catalogue](../operations/catalogue.md)) |
| `batch-preview`, `batch-inspect`, `batch-run` | [Foreground evidence batches](#foreground-evidence-batches) |
| `report-observe`, `report-inspect`, `cover-inspect` | Produce and reopen metadata reports and cover previews ([metadata report](../operations/metadata-report.md)) |
| `proposal-create`, `proposal-inspect` | Create and reopen a proposal set ([proposal set](../operations/proposal-set.md)) |
| `case-admit`, `case-inspect` | Admit and reopen a review case ([review case](../operations/review-case.md)) |
| `decision-preview` | Read-only shadow-decision preview |
| `contained-reviewer` | Text-only contained reviewer |
| `cover-packet-review`, `cover-identity-report`, `cover-observation-report`, `cover-policy-report` | [Cover packet scenarios](#cover-packet-scenarios) |

Adding an entry needs a typed request, an explicit branch in `execute`,
catalogue registration and focused tests. There is no dynamic discovery,
plugin, arbitrary module or shell execution, workflow language or DAG
execution.

## Running a scenario

Call the typed API from contributor code or a focused test:

```python
from pathlib import Path

from mechanical_librarian_workbench import ReportRequest, WorkbenchContext, execute

context = WorkbenchContext(Path(".workbench/report-inspection"), input_class="test")
result = execute(context, ReportRequest("inspect", report_id="report_<digest>"))
print(result.render("summary"))  # or canonical "json"
```

Prefer a temporary context, which deletes itself on exit:

```python
with WorkbenchContext.temporary() as context:
    result = execute(context, typed_request, test_operations)
```

Every path resolves beneath the context's workspace. A successful scenario
returns `WorkbenchResult`. Its summary and JSON carry the scenario name,
completion status, artifact ID, byte count and SHA-256 digest. Large output
stays in the local artifact directory. Operation errors cross one redacting
boundary and become a closed `WorkbenchFailure`. Do not print exception causes
or raw provider data.

The persistent `.workbench/` root is git-ignored. Never move private output
into a tracked path.

## Authority and input gates

- **Input class.** A context accepts `synthetic`, `test`, or `private-corpus`.
  A private corpus needs `approved_private_corpus=True` at construction. `/`,
  anything under `/data`, path traversal, production target classes and
  unknown input classes fail before execution. There is no force flag or
  production bridge.
- **Application contracts still apply.** The workbench does not relax
  exact-current, credential, parser, artifact or write-safety checks.
- **Network.** Provider and contained-reviewer calls require an
  `IsolatedNetworkProfile`. Its identifier, sanitized-input assertion and
  authority assertion bind to the SHA-256 of the configuration, manifest or
  reviewer-profile files the operation uses. These authority files are
  limited to 1 MiB and checked before decoding. Configuration and manifests
  run from owner-private snapshots of the admitted bytes. A durable attempt
  record holding those identities is written before each network operation.
  Proposal-set creation and review-case admission pass the same gate because
  their exact-current checks read the target service.
- **Targets and providers.** Target and provider configuration admits only
  loopback or `.invalid` origins. Credential, TLS, password and media paths
  must sit inside the workspace. Enabled fixed-origin metadata providers are
  refused because they cannot be rebound to a fake; inject the focused fake
  operation from the test instead. Never point the workbench at a production
  library or service.
- **Files.** Workspace, authority, attempt and artifact directories and their
  files are owner-only. Unsafe modes, owners, links or conflicting
  digest-addressed files are refused, never repaired.

## Foreground evidence batches

The batch entries run the report operation over a frozen selection, one item
at a time. The selection, provider limits, run behaviour and authority are in
[foreground ebook evidence batches](../operations/ebook-evidence-batches.md).

The manifest file rules:

- The manifest directory must be private to the operator.
- `batch-preview` creates the manifest with an exclusive no-follow open at an
  absent path.
- `batch-inspect` and `batch-run` read through a regular no-follow descriptor
  and check strict UTF-8, canonical bytes, schema, bounds and the
  content-addressed ID before any report authority is constructed.
- There is no lock or background state; run behaviour and recovery are in the
  linked guide.

## Cover packet scenarios

These entries reopen one exact metadata report and work on its bound
`preview-320x480-rgba-png-v1` cover previews. The installed application uses
the same packet, identity, observation and policy modules
([ADR-0049](../architecture/decisions/0049-owner-confirmed-cover-considerations.md)).

- **`cover-packet-review`** builds a `cover-review-packet-v2` packet and sends
  it to the contained reviewer. It and `cover-observation-report` share the
  only image-capable reviewer path; `contained-reviewer` takes no images. A
  packet holds at most 12 previews, 1 MiB each and 8 MiB in total. It uses
  opaque references and carries no target, library, service, path, URL,
  credential, raw provider response or provider work or edition identity. Pixel-identical previews collapse to one
  candidate, the incumbent is kept, and every omission is recorded. Ordering,
  deterministic PNG re-encoding, sealed image transport and admission rules
  are in [ADR-0045](../architecture/decisions/0045-bind-report-cover-packets.md).
- **`cover-identity-report`** classifies every retained candidate locally as
  `verified_manifestation_match`, `verified_work_match`, `plausible_match`,
  `unknown` or `conflict`. It sends no provider request and never treats image
  content as identity evidence. `plausible_match` is not verified identity.
  The rules are in ADR-0045.
- **`cover-observation-report`** asks the contained reviewer for closed,
  policy-neutral visual observations, one row per admitted candidate. The
  vocabulary is fixed in
  `src/mechanical_librarian/application/cover_observation.py` and designed in
  [ADR-0046](../architecture/decisions/0046-observe-report-bound-covers.md).
  Missing, repeated, invented or out-of-vocabulary rows are rejected. The
  report holds no title, creator, source identity, policy, ranking or winner.
  New reports are `cover-observation-report-v3`; v1 and v2 reports remain
  readable.
- **`cover-policy-report`** reopens the retained report and packet,
  recomputes the identity report from that evidence, and ranks only
  `verified_manifestation_match` and `verified_work_match` candidates
  deterministically
  ([ADR-0059](../architecture/decisions/0059-deterministic-cover-election.md)).
  The result is `recommended` or `abstained`, with one closed reason.
  `plausible_match`, `unknown` and `conflict` candidates are never eligible. A
  supplied observation report must bind to the exact packet and reviewer
  profile and is retained, but neither the observations nor the three
  `cover-policy-v1` booleans (avoid promotional text, avoid tie-in branding,
  prefer minimal composition) can admit, exclude or reorder a candidate. It
  never dispatches a reviewer.

The reviewer instruction is the only authority. Packet content, provider
metadata, images and visible text are hostile evidence.

The image-capable reviewer retries once, after one second, only for a local
CLI abort: exit code 134, bounded strict JSONL with one `turn.started` and no
terminal event, and non-empty bounded stderr. The retry uses the same request
and profile in a fresh job and child process, and only if the reviewer
credential is unchanged; a changed credential fails as an authentication
failure. Every other runtime, schema,
authentication, setup, sandbox, output or timeout failure is terminal.

## Tests

Focused tests cover catalogue reach, fail-closed inputs, private-corpus
approval, network attempt records, temporary-state removal and redaction:

```text
uv run pytest tests/unit/test_runtime_workbench.py
```

Exact-artifact qualification (`scripts/qualify_exact_artifact.py`) proves the
installed wheel cannot import the workbench, and container lifecycle
qualification (`scripts/qualify_container_lifecycle.py`) proves the same for
the image ([testing strategy](testing-strategy.md)).
