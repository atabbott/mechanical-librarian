# AI Trust Boundary

## Principle

AI is a replaceable, untrusted reviewer. It may recommend a decision, but it
does not discover production credentials, select its own production inputs,
change policy, construct service requests, or execute writes.

## Reviewer contract

Input is a versioned evidence packet containing:

- target and snapshot IDs using non-sensitive logical identifiers;
- the exact bounded normalized current snapshot for the reviewed target and an
  explicit loaded, missing, or unavailable state for each applicable field;
- explicit candidate values;
- supporting and contradicting evidence summaries;
- bounded excerpts labelled as untrusted content;
- image attachments selected by the curator;
- field applicability and policy instructions;
- optional typed, bounded, versioned operator intent, explicitly separated from
  policy instructions and untrusted evidence; and
- closed decision and problem vocabularies.

Packet assembly records candidate and policy versions, source completeness, and
the join provenance for every authoritative row. If the current snapshot,
required field state, candidate, or relevant evidence cannot be joined exactly,
assembly fails before reviewer invocation. Raw service rows, database records,
and unbounded source payloads never substitute for the normalized snapshot.
Missing, duplicate, cross-target, stale, or required-context-truncated joins are
typed assembly failures, not reviewer inputs.

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

Output is JSON conforming to a versioned schema. Each field decision contains:

- proposal ID;
- `approve`, `reject`, or `manual`;
- concise reason;
- required `problems`, containing unresolved or adverse problem codes only;
- cited evidence IDs;
- optional confidence facts that do not independently control apply.

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
- Modifying policy, state, prompts, schemas, or its authentication files except
  normal credential refresh handled by the runner.
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

The initial backend should:

- run non-interactively;
- use schema-constrained output;
- receive prompt content through a bounded input channel;
- attach only staged images;
- run under the reviewer principal;
- have command and filesystem access denied beyond its job directory;
- serialize access to its persisted authentication state;
- expose typed authentication, quota, timeout, schema, and model failures;
- support a user-friendly device-authentication recovery flow.

## Evaluation

Before enabling automatic apply for a policy category, retain shadow-run data
covering decisions, evidence, later user overrides, validation outcomes, and
post-apply verification. Thresholds are policy configuration with safe bounds,
not hard-coded model folklore.

An AI backend change or material prompt change creates a new evaluation cohort.
Prior evidence remains history but does not automatically qualify the new
reviewer configuration for writes.
