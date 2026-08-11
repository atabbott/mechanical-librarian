# Contributor workbench

The installed `mechanical-librarian` command is a production runtime surface and
supports only `review-serve`. The repository-only `workbench/` package retains
the evidence, scenario, inspection, and contained-reviewer workflows needed for
development. It is outside `src/`, has no installed entry point, and is excluded
from the wheel and container.

Run its catalogue from a checkout with:

```text
PYTHONPATH=src:workbench uv run python -m mechanical_librarian_workbench --format summary
```

The catalogue is closed and code-extensible. Each entry names a small typed
request and one or more existing application composition operations. Adding a
scenario requires a typed request, an explicit branch in `execute`, catalogue
registration, and focused proof. Dynamic discovery, plugins, arbitrary module or
shell execution, workflow languages, and general DAG execution are deliberately
absent.

The generic contained-reviewer entry is image-free. The one image-capable entry
is `cover-packet-review`. It accepts a report ID,
reopens only that exact report, and reads only its bound
`preview-320x480-rgba-png-v1` artifacts. The packet uses opaque references and
contains no target, library, service, path, URL, credential, raw provider
response, or provider work/edition identity. Digest duplicates collapse to one
candidate; the inspectable incumbent is retained; other candidates are
deterministically ordered using closed identity designation, original-cover
technical dimensions, and source diversity, with every omission recorded. The
decoded preview dimensions are separate exact binding facts and do not alter
that quality order. A digest-equal group uses the canonical best observed
original dimensions by pixel area, width, then height, independent of source
order. The fixed limits are 12
previews, 1 MiB each, and 8 MiB total. It is not a generic attachment surface.

`cover-identity-report` replays that same exact report and packet locally and
classifies every retained candidate before any aesthetic or policy experiment.
Its five closed outcomes are `verified_manifestation_match`,
`verified_work_match`, `plausible_match`, `unknown`, and `conflict`. The output
contains only opaque report/packet/candidate/evidence references, classifier
identity, and fixed reason codes. Unrelated identifiers and closed retained
bibliographic contradictions produce `conflict` before verification; missing or
ambiguous identity evidence remains `unknown`. The scenario neither sends a
provider request nor reads images as identity evidence.

## Executing a scenario

Call the typed API from contributor code or a focused test. For example, an
offline report inspector is constructed and executed as follows:

```python
from pathlib import Path

from mechanical_librarian_workbench import (
    ReportRequest,
    WorkbenchContext,
    execute,
)

context = WorkbenchContext(Path(".workbench/report-inspection"), input_class="test")
result = execute(context, ReportRequest("inspect", report_id="report_<digest>"))
print(result.render("summary"))  # or canonical "json"
```

All paths resolve beneath the explicit workspace. Temporary contexts are
preferred and delete themselves on exit:

```python
with WorkbenchContext.temporary() as context:
    result = execute(context, typed_request, test_operations)
```

Every successful scenario returns `WorkbenchResult`. Its summary and canonical
JSON contain the same scenario name, completion status, artifact identifier,
artifact byte count, and SHA-256 digest. Large output bytes remain in the local
artifact directory. Operation JSON retains the valid field order emitted by its
owning application contract; the workbench does not impose a second universal
key-ordering rule. Operation exceptions cross one redacting boundary and
become a closed `WorkbenchFailure`; do not print exception causes or raw provider
data.

## Authority and input gates

Contexts accept only synthetic fixtures, explicit test workspaces, or a private
corpus whose approval is stated at construction. `/`, `/data`, path traversal,
production target classes, and unknown input classes fail before execution.
There is no force flag or production bridge. The workbench does not relax the
application's exact-current, credential, parser, artifact, currentness, or
write-safety contracts.

Provider and contained-reviewer calls additionally require an
`IsolatedNetworkProfile`. Its identifier, sanitized-input assertion, and bounded
authority assertion are validated and bound to the exact SHA-256 identities of
the configuration, manifest, or reviewer-profile files used by the operation.
Authority files are size-bounded before decoding. Configuration and manifest
operations execute only from owner-private snapshots of the exact admitted
bytes. Absolute private-file references are rebound to matching owner-private
snapshots in the execution configuration; contained-reviewer operations receive
the admitted bytes directly. For a cover packet, the same durable attempt also
binds the canonical ordered opaque-reference/preview-digest mapping. Exact
preview bytes are copied to sealed anonymous descriptors, bound read-only,
supplied as repeatable ordered `--image` arguments, and closed with the job.
The cover entry accepts only canonical `cover-review-packet-v1` input whose
content-bound packet reference, every closed candidate/provenance/context and
omission field, byte/dimension and incumbent facts, omission reasons, and limits
have passed one shared producer/admission validator. It also enforces the
canonical candidate order, reason-specific omission preconditions, domain
privacy-safe context text, and exact ordered candidate/image equality.
Every admitted preview is fully parsed and decoded through Pillow before it is
mounted: its complete PNG structure and checksums, single-frame PNG format,
RGBA mode, maximum 320-by-480 dimensions, and exact declared preview dimensions
must all agree. The closed deterministic chunk profile accepts only exact IHDR,
one or more consecutive IDAT chunks containing exactly one completed zlib
stream with no trailing data, and final IEND. ICC profiles, palette,
transparency, every other ancillary, and every unknown chunk are rejected before
mount; their bytes never cross the reviewer boundary.
Forged signatures, corrupt or truncated streams, trailing bytes, unsafe
dimensions, and decoder warnings or failures become the same value-free typed
format failure.
Indistinguishable normalized provenance rows collapse to one. Every distinct
source kind represented by a digest-equal group participates in diversity
priority and counts; no lexicographic representative can hide another source.
The generic
request and adapter method have no image member or argument. A changed,
repeated, unknown, non-PNG, over-bound, or unbound image fails closed. Only the
bounded Instruction is authoritative; canonical input, provider metadata,
images, and OCR text are hostile evidence and never instructions.

Admitted image bytes are copied into anonymous Linux memory descriptors, hashed
again, and sealed against write, growth, shrink, and seal removal before process
creation. The sandbox binds those descriptor identities directly. A dispatch
callback or another same-UID process therefore cannot replace or mutate the
bytes between recorded admission and child release.
The image-capable adapter also reconstructs and fully re-admits the complete
packet and exact ordered image tuple at its own boundary, so directly importing
or constructing a private request shape cannot bypass packet validation.

Duplicate omissions are finalized after selection and can reference only a
retained representative. If byte, aggregate, or shortlist limits exclude the
whole digest group, every member records that exclusion reason instead.
Target/provider configuration additionally admits only loopback or `.invalid`
origins and confines credential, TLS, password, and media paths to the workbench
workspace. The production composition refuses every enabled fixed-origin
metadata provider because those origins cannot be rebound to an isolated fake;
provider experiments must inject the focused fake operation used by their test.
A canonical attempt record containing those input identities is
durably created before each network operation. Use only fake or explicitly
isolated services and sanitized inputs; never point the workbench at a
production library or service.

Persistent workspace, authority, attempt, and result-artifact directories are
owner-only, as are their files. Unsafe pre-existing modes, owners, links, or
conflicting digest-addressed files are refused instead of repaired. ProposalSet
creation and review-case admission pass through the same isolated-configuration
and durable pre-egress gate because their exact-current checks can read the
target service and selected media.

## Qualification

Focused tests prove the full catalogue reaches the shared application
operations, action-specific inputs fail closed, private inputs require approval,
network attempts are recorded, temporary state is removed, and diagnostics are
redacted. The documented persistent `.workbench/` root is repository-ignored;
never relocate private outputs into a tracked path. Exact-artifact qualification additionally builds the wheel and
container, invokes installed help from outside the checkout, and proves the
workbench package cannot be imported or executed there.

Historical sealed-v2 qualification attempted 17 development and
qualification-holdout packets. Its aggregate-only receipt records 17 typed
`contained_reviewer_invocation_insufficient` failures, zero ordered-reference
mismatches, zero admitted image or reviewer-output bytes, p50 129 ms, p95 160
ms, successful cleanup, and a failed sandbox aggregate.

The current exact-head admission-only replay instead examined 17 packets: eight
were eligible and nine were rejected before dispatch with the typed
`icc_bearing_preview_rejected` result; there were no other rejection classes.
No model rerun occurred. Sealed v2 retained no per-packet outcome or latency
mapping, so the eligible subset cannot be rebound to its historical outcomes or
latencies (`sealed_subset_mapping_insufficient`). Historical latency must not be
attributed to the current head. The required transport bound therefore **DID
NOT QUALIFY**. There was no retry or limit, profile, model, or threshold change.
The closed transport seam remains synthetically verified and
source-only/report-only; this evidence is not runtime activation. #501 / 0.15.1
remains unpromoted.
