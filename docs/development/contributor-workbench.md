# Contributor workbench

The installed `mechanical-librarian` command is a production runtime surface
with the closed review, policy-preview, owner cover-review, and read-only
activity commands documented in the deployment guide. The repository-only
`workbench/` package retains broader evidence, scenario, inspection, and
contained-reviewer workflows needed for development. It is outside `src/`, has
no installed entry point, and is excluded from the wheel and container.

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
response, or provider work/edition identity. Normalized-pixel-equal previews
collapse to one candidate; the inspectable incumbent is retained; other
candidates are deterministically ordered using closed identity designation, original-cover
technical dimensions, and source diversity, with every omission recorded. The
decoded preview dimensions are separate exact binding facts and do not alter
that quality order. A normalized-pixel-equal group uses the canonical best
observed original dimensions by pixel area, width, then height, independent of
source order. The fixed limits are 12
previews, 1 MiB each, and 8 MiB total. It is not a generic attachment surface.

`cover-identity-report` replays that same exact report and packet locally and
classifies every retained candidate before any aesthetic or policy experiment.
Its five closed outcomes are `verified_manifestation_match`,
`verified_work_match`, `plausible_match`, `unknown`, and `conflict`. The output
contains only opaque report/packet/candidate/evidence references, classifier
identity, and fixed reason codes. Unrelated identifiers, edition/work
contradictions, and incompatible manifestation publication/format evidence
produce `conflict` before provider verification. An exact report-and-
packet-bound EPUB cover verifies that source manifestation only, never a
provider cover or Audiobookshelf incumbent. An otherwise unresolved provider
cover is `plausible_match` only when its exact edition/work pair is retained by
an ISBN lane (or an unmapped frozen legacy discovery report) and it has positive title agreement
and no unopposed creator/contributor disagreement or deterministic work
contradiction. Exact retained creator/contributor overlap counts as agreement.
Missing creator values are neutral because cover-only discovery may not retain
them; pairwise disagreement alongside creator/contributor agreement and
edition-specific metadata disagreement do not erase title support.
`plausible_match` is not verified identity; no title agreement, unopposed
creator/contributor disagreement, or unbound provider identity remains
`unknown`. The scenario neither sends a provider request nor reads images as
identity evidence.
Every candidate's bound source previews are reopened through the report reader,
source-digest verified, and re-normalized to the candidate transport identity;
references and normalized provenance are also re-derived from the report.
Matching preview bytes cannot substitute a packet from another report. Date
evidence is not identity plausibility support or a veto, and a default provider
cover with explicit work identity can verify only the work.

`cover-observation-report` reuses the same exact report-bound packet and sole
image-capable reviewer entry, but asks only for four visible, policy-neutral
properties. Its closed vocabulary is:

- medium: `photographic`, `illustrated`, `typographic`, `mixed`, `unknown`;
- composition: `minimal`, `dense`, `unknown`;
- promotional text: `present`, `absent`, `unknown`;
- tie-in branding: `present`, `absent`, `unknown`.

Definitions are fixed in
[ADR-0046](../architecture/decisions/0046-observe-report-bound-covers.md).
The reviewer must return exactly one row for every admitted candidate. Local
validation rejects missing, repeated, invented, extra, or non-vocabulary
values and rechecks the complete contained-reviewer result envelope. The
immutable report binds the packet and exact candidate preview digest/variant,
plus opaque taxonomy, instruction, output-schema, and observer identities. It
contains no title, creator, source identity, provider value, owner policy,
eligibility, ranking, preference, or winner.

The reopened 0.18.0 requalification used independent blinded reference
judgements frozen before tested outputs were opened; these are review
judgements, not claimed human ground truth. The first development configuration
admitted all 50 candidates and cleared the predeclared useful agreement
threshold for every field, so no second prompt/packaging configuration was run.
A shuffled seeded 20-case repeat matched every field and the joint result in all
20 cases. The fresh one-shot final cohort admitted 48 of 50 candidates with zero
binding or safety violations. This proves bounded overall-agreement usefulness
and repeatability for the source-only workbench; it does not prove every
class-specific policy use or runtime readiness. Development minimal-positive
precision was 11/14 (78.57%) and reference-minimal recall was 11/15 (73.33%), a
mixed and sparse class result. Final minimal-positive precision was 15/21
(71.43%), below the useful band, and tie-in branded-positive recall was 2/4
(50%). The later full-packet development policy replay found 90% exact agreement
for minimal preference alone and 98% for tie-in avoidance alone. Promotional
avoidance remained mixed at 78% exact across its four combinations. The later
reusable engineering benchmark diagnosed its initial low coverage as Rust
allocation aborts under a 512 MiB child limit. A 1 GiB profile admitted all
10 packets and 36 candidates. The baseline prompt was exact on 74/80 policy
decisions; one bounded composition clarification reached 76/80 and every policy
combination reached at least 8/10, with zero retries or violations. This is
reusable-benchmark evidence rather than unseen-holdout generalisation.

`cover-policy-report` is a deterministic consumer of the exact retained
report, cover packet, closed observation report, the authority-bound reviewer
profile that produced it and `cover-policy-v1`. It
recomputes the local identity report from that retained evidence rather than
accepting caller-supplied identity outcomes, and recomputes the exact observer
reference instead of accepting a merely well-shaped reference.
Callers can avoid promotional text, avoid tie-in branding and prefer minimal
composition through three booleans; no prompt-authored or free-form policy is
accepted. Conflict and unknown-identity candidates are excluded individually;
identity abstains only when none remain. Policy-relevant unknown observations
still abstain, and a recommendation can name only one exact acceptable packet
reference. When observations are unavailable, the closed result is
`observations_unavailable`; the scenario never dispatches another reviewer.
The current development proof reused 50 retained packet observations covering
148 candidates and all eight policies. Exact acceptable-set, recommendation and
whole-decision agreement were 88.5%, 89% and 86%, respectively, with every
combination at or above the predeclared potentially-useful band and zero safety
or binding violations. The reusable 10-packet, 36-candidate engineering
benchmark complements that development proof. Its final prompt-bound run
admitted every candidate and agreed on 78/80 acceptable sets and
recommendations, 80/80 statuses and 76/80 exact whole decisions. Field
agreement was 26/36 composition, 33/36 medium, 36/36 promotional text and
35/36 tie-in branding. Composition remains the least stable field: its two
blinded human reviews disagreed on 12/36 labels.

The cover entry has one deliberately narrow recovery path for a locally proven
CLI abort: only return code 134 with bounded strict JSONL that contains one
`turn.started` and no completed or failed terminal event, plus bounded nonempty
stderr, gets one fixed one-second retry. It reuses the exact admitted request
and profile but creates a fresh job and child process inside the same bound
adapter and authentication lock. Its source-only envelope contains value-free
dispatch and first-failure code/count/digest evidence. A
near miss and every generic runtime, schema, auth, setup, sandbox, malformed
output or timeout failure stays terminal.
New observation reports are `cover-observation-report-v2`; frozen v1 reports
remain readable under their exact frozen instruction identity and contain no
retry evidence.

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
preview bytes are copied to sealed anonymous descriptors. An image larger than
the profile's child file limit fails before dispatch. After gated release, the
child reopens and verifies each descriptor with an independent read cursor;
Bubblewrap copies those cursors through descriptor-data mounts into private
read-only sandbox files. Those files are supplied as repeatable ordered
`--image` arguments, and the descriptors are closed with the job.
The cover entry accepts only canonical `cover-review-packet-v2` input whose
content-bound packet reference, every closed candidate/provenance/context and
omission field, byte/dimension and incumbent facts, omission reasons, and limits
have passed one shared producer/admission validator. It also enforces the
canonical candidate order, reason-specific omission preconditions, domain
privacy-safe context text, source-to-derived transport bindings, and exact
ordered transport/image equality. Packet preparation verifies each exact
report-bound source digest, strips all ancillary/profile metadata, decodes one
RGBA frame within the existing bounds, and deterministically re-encodes the
same pixels. The platform-independent encoder writes filter byte zero for each
row and one zlib stream using fixed uncompressed DEFLATE blocks of at most
65,535 bytes, with explicit Adler-32 and PNG CRC values; output bytes do not
depend on Pillow, libpng, or platform compression choices. Source
reference/digest bindings remain separate from the derived transport
reference/digest.
Every admitted transport is fully parsed and decoded through Pillow before it is
mounted: its complete PNG structure and checksums, single-frame PNG format,
RGBA mode, maximum 320-by-480 dimensions, and exact declared preview dimensions
must all agree. The closed deterministic chunk profile accepts only exact IHDR,
one IDAT containing exactly one completed zlib
stream with no trailing data, and final IEND. ICC profiles, palette,
transparency, every other ancillary, and every unknown chunk are absent from the
canonical derived transport; their source bytes never cross the reviewer
boundary. Admission repeats the deterministic encoding and requires exact byte
equality.
Forged signatures, corrupt or truncated streams, trailing bytes, unsafe
dimensions, and decoder warnings or failures become the same value-free typed
format failure.
Aggregate retained replay under macOS and canonical Linux/Pillow 12.3 produced
exact byte/digest parity for 147/147 affected previews while preserving every
pixel and dimension. The same frozen 10 packets containing 50 candidates
admitted 10/10 on both platforms without provider or model calls, production
access, or writes.
Indistinguishable normalized provenance rows collapse to one. Every distinct
source kind represented by a normalized-pixel-equal group participates in diversity
priority and counts; no lexicographic representative can hide another source.
The generic
request and adapter method have no image member or argument. A changed,
repeated, unknown, non-PNG, over-bound, or unbound image fails closed. Only the
bounded Instruction is authoritative; canonical input, provider metadata,
images, and OCR text are hostile evidence and never instructions.
The cover observer additionally requires visible properties only and uses
`unknown` rather than inferring identity, policy, quality, preference,
eligibility, ranking, or a winner.

Admitted image bytes are copied into anonymous Linux memory descriptors, hashed
again, and sealed against write, growth, shrink, and seal removal before process
creation. After gated release, the child reopens each sealed descriptor read-only
through its own `/proc`, verifies its identity, seals, size, and digest, rewinds
the independent open-file description, and replaces the inherited descriptor
before limits and exec. Bubblewrap then copies from those child-private cursors
through descriptor-data mounts into private read-only sandbox files. A dispatch
callback or another same-UID process therefore cannot replace or mutate the bytes
or steer the copy by seeking a shared inherited cursor.
The image-capable adapter also reconstructs and fully re-admits the complete
packet and exact ordered image tuple at its own boundary, so directly importing
or constructing a private request shape cannot bypass packet validation.

Duplicate omissions are finalized after selection and can reference only a
retained representative. If byte, aggregate, or shortlist limits exclude the
whole normalized-pixel-equal group, every member records that exclusion reason
instead. Target/provider configuration additionally admits only loopback or `.invalid`
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

Diagnosis showed that the historic attempt exited before reviewer execution:
Bubblewrap could not bind sealed images through `/proc` paths. The corrected
adapter instead supplies verified child-private descriptor cursors through
descriptor-data mounts. A no-model five-image Linux proof reproduced exact bytes
inside the sandbox while the parent repeatedly sought its inherited descriptors
to EOF. A text-only reviewer probe then exposed independently bounded private
profile limits that permitted authentication but starved inference; a 512 MiB
memory and 32-task isolated envelope completed the admitted probe without
changing model, prompt, network, image, output, provider, or corpus authority.

The corrected exact implementation qualified one retained real development
packet with five exact previews: five ordered observations were admitted with
zero reference mismatch in 16.263 seconds, within the 180-second supported
bound, and ephemeral cleanup passed. The source-only/report-only transport
therefore **QUALIFIED**. This is transport and schema-admission evidence, not a
semantic-quality or runtime-activation claim. #501 / 0.19.0
remains unpromoted.

The subsequent policy-neutral observation qualification used only retained
report-bound artifacts. Its development hypothesis was that the existing
sealed-image packaging, fixed instruction, closed schema, and corrected bounded
profile would produce admitted observations with at least 75% exact agreement
for each field and stable repeats. The first configuration admitted 50/50
candidates at 84–98% per-field exact agreement; its seeded 20-case repeat was
20/20 identical per field and jointly. These results stopped development after
one of the permitted three configurations.

Two disjoint final cohorts supplied no semantic evidence: a broad private JSON
discovery scanner opened final-reference files before the first could execute,
without exposing values to tested or model-visible output, and the second was
spent by credential-mount setup. After diagnosis and corrected preflight, the
next fresh disjoint one-shot cohort admitted 48/50 candidates at 83.33–97.92%
per-field agreement and 75% all-four agreement. Its two failures occurred after
dispatch and before result admission; no exact shared cause was proven, and the
4% candidate missingness rate remains within the frozen 5% candidate-coverage
tolerance, but its bias magnitude is unknown. The 50 selected candidate rows
came from 29 distinct packets, including 25 multi-choice packets, and rows from
the same packet are correlated because one admitted packet result can score
multiple selected rows. The two failures were packet invocations, but aggregate
receipts do not retain enough mapping to report distinct successful or failed
packet counts, so packet-dispatch coverage is unavailable. The primary
field-level gate passes and survives treating both missing cases as
disagreements; the 75% joint admitted-case result falls
to 72% under that sensitivity and is not robustly useful across all dispatched
cases. Overall composition agreement was mixed, but minimal-positive precision
was only 15/21 (71.43%) despite 15/17 (88.24%) reference-minimal recall;
prefer-minimal usefulness remains separately unproven. Overall tie-in agreement
was high, but branded-positive recall was only 2/4 (50%) against 44/44
absent-case specificity; avoid-tie-in policy usefulness
therefore remains separately unproven. The run recorded zero binding, safety,
provider, production-access, or write violations. The full aggregate selection,
confusion, unknown-rate, hypothesis, latency, repeatability, recovery,
agreement, and interpretation
record is in
[semantic cover corpus readiness](semantic-cover-corpus-readiness.md).
