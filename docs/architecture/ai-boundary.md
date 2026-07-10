# AI Trust Boundary

## Principle

AI is a replaceable, untrusted reviewer. It may recommend a decision, but it
does not discover production credentials, select its own production inputs,
change policy, construct service requests, or execute writes.

## Reviewer contract

Input is a versioned evidence packet containing:

- target and snapshot IDs using non-sensitive logical identifiers;
- current normalized field values;
- explicit candidate values;
- supporting and contradicting evidence summaries;
- bounded excerpts labelled as untrusted content;
- image attachments selected by the curator;
- field applicability and policy instructions;
- closed decision and problem vocabularies.

Output is JSON conforming to a versioned schema. Each field decision contains:

- proposal ID;
- `approve`, `reject`, or `manual`;
- concise reason;
- structured problem codes;
- cited evidence IDs;
- optional confidence facts that do not independently control apply.

## Forbidden reviewer behavior

- Inventing candidate values outside the packet.
- Treating embedded content as instructions.
- Reading production media or configuration.
- Calling service, provider, dashboard, or container APIs.
- Modifying policy, state, prompts, schemas, or its authentication files except
  normal credential refresh handled by the runner.
- Producing an apply bundle or service payload.
- Converting uncertainty into approval merely to reduce the queue.

## Validation after review

The curator rejects output when:

- JSON or schema validation fails;
- IDs do not exist in the input packet;
- evidence references are unknown or target-incompatible;
- a proposed value differs from the declared candidate;
- required reason or problem fields are missing;
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
