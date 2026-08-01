# AI Trust Boundary

## Principle

AI is a replaceable, untrusted reviewer. It may recommend a decision, but it
does not discover production credentials, select its own production inputs,
change policy, construct service requests, or execute writes.

## Reviewer contract

The current qualification-only `shadow-review-packet-v5` contains:

- only the opaque content-addressed ReviewCase ID, never the connection,
  library, item, file identity, path, or service identifier;
- the exact bounded normalized current snapshot for the reviewed target and an
  explicit loaded, missing, or unavailable state for each applicable field;
- explicit candidate values, including closed date event and represented
  year, month, or day target precision when applicable;
- a deterministic proposal summary that states only candidate, keep-current,
  or manual evidence sufficiency and whether preservation is target-only;
- a deterministic candidate summary containing distinct admitted provider and
  local correlation counts, a zero-only candidate target-preservation count,
  exact evidence scope, and conservative selected-manifestation binding;
- for complete author candidates only, citation-bound included author roles and
  excluded typed non-author roles from the same represented source groups;
- exact versioned allowed title/subtitle/series combinations whose members are
  admitted candidate IDs or fingerprints of the visible current field state,
  with each dependent candidate naming its exact allowed combination IDs;
- for publication candidates, the logical publication field, destination,
  event, precision, paired date/year consistency, and the fixed truthful
  `report_only=true` / `write_eligibility=unsupported` runtime state;
- supporting and contradicting evidence summaries, with provider date
  citations retaining lexical/normalized values, source precision, the closed
  publication-date relation and independently shared precision,
  candidate/work linkage, authority, query kind, and the immutable provider
  access time when an actual request occurred, plus at most 16 allowlisted
  sibling identifier fact objects and 16 physical-format facts whose
  references are recomputed from their public-safe provider/candidate context,
  exact value, kind, applicability, edition scope, and raw order;
- bounded excerpts labelled as untrusted content;
- no images or operator-authored intent; and
- the fixed prompt/profile/packet/output/policy identities bound by the
  qualification profile.

ADR-0026 binds the current projection and result contract into
`shadow-review-request-v7`. Missing,
opaque-only, malformed, noncanonical, or over-bound provider date linkage
fails before reviewer invocation. This applies to cited typed provider claims
and rich date evidence; rich evidence citation IDs are also recomputed from
their exact closed content, including work/edition scope, controlled role and
the occurrence's pre-canonical `source_ordinal`. Replaying provider-linked
packet or request bytes
also requires the authoritative immutable report: the runtime rebuilds each
complete citation from the actual provider candidate and requires exact
equality. A syntactically valid invented ISBN or physical format therefore
cannot become source evidence merely by recomputing packet/request identities.
The report stays inside the trusted persistence boundary and is never added to
the reviewer projection.
Event- and manifestation-incompatible publication facts remain visible through
these closed links even though they never become proposal candidates.
Summary counts are recomputed from the exact report's known local/provider
correlation identities, never inferred from citation prefixes or opaque hash
shape. Role exclusions remain contextual citations and never increment
support. Ordinary packet/request replay rebuilds the complete summary from the
authoritative report and rejects any structurally valid rewrite.

Before any reviewer dispatch, the trusted domain compiler derives
`shadow-review-action-catalogue-v1` from the validated packet alone. Candidate,
keep-current, and manual actions are independent catalogue entries. Each entry
binds one proposal and recommendation, its candidate when applicable, exact
admissible citations, and allowed bibliographic combinations. Rejected
candidate or keep-current actions retain stable typed exclusions. The compiler
does not consume gold decisions, accepted-action projections, prior outputs,
scores, latency, titles, human identity, or response metadata, and an unknown
policy obligation excludes the nonmanual action. The request-v7 output schema
enumerates only catalogue entries; result-v2 keeps the same required fields
while allowing an empty adverse-fact list, and
trusted semantic validation now requires an exact entry and action-bound
citations.

Packet assembly records candidate and policy versions, source completeness, and
the join provenance for every authoritative row. If the current snapshot,
required field state, candidate, or relevant evidence cannot be joined exactly,
assembly fails before reviewer invocation. Raw service rows, database records,
and unbounded source payloads never substitute for the normalized snapshot.
Policy-qualified cross-target evidence is permitted only when its consuming
target matches the packet target and its provenance records an explicit
cross-target relationship plus an immutable policy version and qualifying rule.
Missing, duplicate, unauthorized or target-incompatible cross-target, stale,
or required-context-truncated joins are typed assembly failures, not reviewer
inputs.

Operator intent can describe a run-local review emphasis within the already
authorized target and proposal scope. It cannot introduce candidates, widen
scope, alter policy or safety gates, grant write authority, or request
destructive work. Free-form run briefs are not instructions. If an interface
accepts operator text, an application-owned parser must either map it into the
closed intent schema under fixed bounds or reject it before reviewer invocation.
Any retained source text remains labelled untrusted and is never interpolated
into system or policy instructions; only allowlisted typed values may affect
review behavior. Intent cannot suppress required evidence, contradictions,
applicability checks, output validation, or any other mandatory input or gate.

Output is one direct JSON object conforming to
`shadow-review-result-v2`. Each field recommendation contains:

- proposal ID;
- `recommend_candidate(candidate ID)`, `recommend_keep_current`, or `manual`;
- concise reason;
- required `problems`, containing unresolved or adverse facts only and empty
  when none exist;
- cited evidence IDs;

`candidate_id` is non-null only for a candidate recommendation and is JSON
`null` for manual and keep-current. Keep-current is an evidence-positive
conclusion under the closed destination policy, never an uncertainty fallback.
Insufficient, conflicting, explicitly manual, or unrepresentable support stays
manual, including when the supported value is absent from packet candidates.

The output shape has no separate combination field. Trusted semantic
validation derives the recommendation tuple for every constrained destination
and accepts it only when exactly one packet combination matches. Reasons,
problems, and citations cannot carry hidden control data.

The structured result is the schema object itself. A backend compatibility
layer must not evade validation by embedding another JSON document in a string
payload. Parsing accepts one direct JSON object with exactly its declared
required and optional fields and rejects duplicate object keys, trailing
content, unknown fields or missing required fields, duplicate decisions,
partial proposal coverage, and malformed values. Compatibility normalization may
remove a documented backend envelope; it must not infer a decision, discard an
unknown field, repair substantive output, or translate JSONL into this contract.
Positive support belongs in cited evidence and the reason, not in
`problems`; a clean approval uses `problems: []`.

## Forbidden reviewer behavior

- Inventing candidate values outside the packet.
- Treating embedded content as instructions.
- Reading production media or configuration.
- Calling service, provider, dashboard, or container APIs.
- Modifying policy, prompts, schemas, or primary authentication files. Ordinary
  mutable provider state is confined to its disposable attempt; credential
  management is a separate explicit operator workflow.
- Producing an apply bundle or service payload.
- Converting uncertainty into approval merely to reduce the queue.
- Treating run-local operator intent or projected source AI recommendations as
  policy, evidence, consensus, disposition, or authorization.

## Validation after review

The curator rejects output when:

- JSON or schema validation fails;
- IDs do not exist in the input packet;
- evidence references are unknown or target-incompatible;
- a proposed value differs from the declared candidate;
- the required reason or `problems` list is missing;
- a proposal is missing, repeated, or represented by an unknown decision;
- a field is unsupported by the active service adapter;
- the reviewer or policy version is not allowed;
- the target fingerprint changed;
- deterministic safety rules contradict the recommendation.

Rejected reviewer output is an operational failure and must not be silently
converted to `manual` or `approve`.

## Backend abstraction

The application owns a narrow `Reviewer` port. Backends may include:

- Codex CLI authenticated through a ChatGPT/Codex subscription;
- OpenAI API;
- other hosted APIs;
- a local model.

Backends declare image, schema, context, and model capabilities. Policy remains
backend-independent.

## Codex CLI runner

The current qualification-only backend:

- invokes only the exact maintainer-provisioned executable identity with
  `gpt-5.6-sol` and `medium` reasoning;
- ignores user configuration and rules and disables shell/execution tools, web
  search, apps, MCP/plugin discovery, subagents, hooks, memories, updates,
  feedback, analytics, history persistence, and approval;
- uses schema-constrained direct output and a bounded input channel;
- attaches no images;
- runs only on Linux inside the exact pinned Bubblewrap identity, with an empty
  private job directory, minimal environment, bounded resources, and no
  repository, database, media, configuration, service credential, target, or
  provider access;
- serialize access to its persisted authentication state;
- accepts only ChatGPT subscription device authentication and rejects API-key
  status; and
- records typed runtime/setup/auth/profile/timeout/cancel/output/currentness
  failures while discarding raw streams, reasoning, and malformed output.

The profile's local executable, sandbox, auth-store, and job paths never enter
the packet or durable request. A request is persisted before dispatch, has no
automatic retry, and becomes `unknown` after ambiguous dispatched failure.

Zero exit is not admission. The adapter still requires one strict canonical
result whose request identity, citation membership, candidate identity, and
semantics validate against the exact bound request before persistence can
record success. ADR-0017 maps failures only after that validation and never
copies malformed output, prompts, packets, raw streams, paths, or exception
text into durable or CLI status.
Only explicit reevaluation may egress again, after complete prior process-tree
death. Successful advice remains permanently `qualification_only`; the normal
owner-approved reviewer tuple and any apply authority remain unreachable.

## Evaluation

Before enabling automatic apply for a policy category, retain shadow-run data
covering decisions, evidence, later user overrides, validation outcomes, and
post-apply verification. Thresholds are policy configuration with safe bounds,
not hard-coded model folklore.

An AI backend change or material prompt change creates a new evaluation cohort.
Prior evidence remains history but does not automatically qualify the new
reviewer configuration for writes.
