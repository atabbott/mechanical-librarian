# Reviewer Backends After the 1.0 Checkpoint

## Purpose and status

This document defines deferred work for additional AI reviewer backends after
the 1.0 checkpoint. It is planning authority for the outcomes and safety
boundaries below, not a statement of current behavior or permission to begin
implementation.

The initial checkpoint plan remains unchanged:

- Codex CLI is the first reviewer backend;
- F11 delivers isolated AI shadow recommendations;
- the current 1.0 outcome set and exit criteria in the
  [delivery-slice plan](delivery-slices.md) remain authoritative; and
- the normal deployment remains one Mechanical Librarian container.

After the 1.0.0 checkpoint is complete, two optional reviewer capabilities may
be scheduled as separate functional slices:

1. a hosted OpenRouter reviewer using an owner-controlled account and exact
   qualified free or paid model configuration; and
2. a reviewer connected to a separately operated compatible model server.

Neither capability depends on the other. Both depend on the completed F11
reviewer contract and isolation boundary, stable continuous operation from F17,
and structured reviewer configuration and secret references. F17 is not a
release cohort. These outcomes receive slice IDs only when maintainers schedule
and explicitly promote them.

## Shared contract

Every future backend implements the application-owned `Reviewer` port and
preserves the [AI trust boundary](../architecture/ai-boundary.md). A backend:

- receives only the versioned bounded reviewer packet and selected staged
  attachments;
- returns one direct closed-schema object with every-and-only proposal
  coverage;
- receives no target-service or metadata-provider credential, main database,
  backup, production media, mutation port, shell, tool, or apply authority;
- cannot select candidates, evidence, policy, prompts, destinations, or write
  operations;
- declares its text, image, context, schema, authentication, and model-identity
  capabilities before work is eligible;
- reports actual model, endpoint, and provider identity plus bounded usage
  metadata through its `Reviewer` result; the application persists requested
  and actual backend, model, endpoint, and provider identity plus prompt,
  evidence, output-schema, policy, and configuration versions as immutable
  provenance;
- treats an identity or material configuration change as a new evaluation
  cohort whose prior qualification does not transfer;
- applies hard input, output, response-size, timeout, concurrency, retry, and
  circuit-breaker bounds; and
- converts authentication, quota, availability, capability, identity,
  transport, timeout, malformed-output, and citation failures into typed safe
  states without inferring or repairing a decision.

Switching between hosted and external endpoints, free and paid models, models,
providers, endpoints, or privacy policies is always an explicit audited
configuration change. No backend may silently fall back across those
boundaries. Inventory, local evidence, deterministic proposals and policy, and
human review remain usable when every AI backend is absent or unhealthy.

The [data-privacy contract](../architecture/data-privacy.md) governs any
external processing. Configured reviewer credentials are write-only,
reviewer-role-scoped secret references and never appear in ordinary exports,
browser readback, logs, audit content, or support bundles.

## Shared semantic qualification

Each future slice independently carries a synthetic and shadow cohort covering:

- clean approve, reject, and manual recommendations;
- same-title/different-author, edition, format, narrator, abridgement,
  dramatization, omnibus, identifier, and cross-media conflicts;
- positive and contradictory evidence, missing context, prompt injection,
  Unicode, multilingual, and bounded long-input cases;
- image-dependent cases for a profile declaring image support;
- exact proposal coverage, closed problem vocabulary, and valid evidence
  citations; and
- malformed, truncated, extra-field, embedded-JSON, tool-call, and repaired
  output attempts.

These are requirements of each slice, not a shared runtime artifact or a
delivery dependency between the slices.

## Deliverable A: OpenRouter reviewer

### OpenRouter deferred outcome draft

**Title:** Deliver a qualified OpenRouter reviewer with owner-controlled
credentials

**Classification:** Deferred after-1.0-checkpoint Feature; High risk;
`Unassigned backlog`; no milestone or functional-slice designation

**Outcome:** An owner opts into disclosed hosted processing, supplies an
OpenRouter credential, selects one exact qualified free or paid reviewer
configuration, and receives validated recommendations through the existing
reviewer contract without changing production authority.

**Dependencies:** F11, F17, the accepted reviewer-isolation decision,
structured reviewer authentication references, and the authenticated owner
configuration surface.

### OpenRouter supported demonstration

1. Configure an owner-controlled OpenRouter credential through a write-only
   value or file-secret reference and inspect redacted status.
2. Select one exact qualified free model configuration. Before accepting its
   matching disclosure or enabling it, attempt an OpenRouter connection test,
   capability, identity, and authentication preflight and reviewer packet;
   prove each is blocked locally with zero OpenRouter requests and zero
   credential transmission.
3. Accept the disclosure matching the free configuration, explicitly enable
   it, perform its preflight, run the synthetic F11 packet, validate its direct
   closed-schema result, and inspect the persisted model, provider, routing,
   usage, and validation provenance.
4. Select one distinct exact qualified paid model configuration and prove the
   free configuration's acceptance does not transfer: the newly selected paid
   configuration's preflight and packet remain blocked locally with zero
   OpenRouter requests and zero credential transmission.
5. Accept the disclosure matching the paid configuration, enable or re-enable
   it as appropriate, perform its preflight, run the packet, and inspect its
   distinct immutable configuration, evaluation cohort, and provenance.
6. Inject missing and invalid credentials, insufficient credit, free-tier
   exhaustion, rate limiting, retired or unavailable models, provider-policy
   mismatch, unsupported structured output, timeout, malformed output, and
   unexpected model or provider identity.
7. Confirm that affected work becomes visibly unavailable or manual while
   deterministic and human workflows continue and no write authority is
   created.

### OpenRouter acceptance criteria

- The image contains no project-owned or shared OpenRouter credential. Every
  installation uses an owner-controlled account and reviewer-scoped secret.
- Before every OpenRouter connection test, capability, identity, or
  authentication preflight, or reviewer request, the backend is explicitly
  enabled and its currently accepted immutable disclosure/configuration
  identity matches the active destination, model, provider, routing, privacy,
  retention, training, region, free-or-paid status, cost or quota behavior,
  sent-data fields and excerpts, and staged attachment or image categories.
  Otherwise the operation blocks locally with zero OpenRouter requests and
  zero credential transmission. The owner-visible matching disclosure itself
  names every bounded sent-data field and excerpt, every staged attachment or
  image category, the processing parties, the privacy, retention, training,
  and region posture, and cost or quota behavior. It explicitly excludes raw
  media, whole works or audio, production paths, and unrelated evidence.
- Any material change to that destination, model, provider, routing, privacy,
  retention, training, region, free-or-paid status, cost or quota behavior,
  sent-data, or attachment configuration invalidates acceptance before the
  next request and requires acceptance of the new matching disclosure.
  Acceptance remains valid for unchanged requests under the same immutable
  configuration identity.
- OpenRouter traffic uses a dedicated reviewer client restricted to the fixed
  allowlisted OpenRouter HTTPS API origin with normal hostname and certificate
  validation. It shares no cookies, headers, credentials, or transport state
  with target-service or metadata-provider clients.
- Redirects are disabled. If a future reviewed transport requires one, every
  destination is revalidated and the bearer credential is never forwarded
  across origins.
- Configuration names an exact qualified model ID and a versioned provider and
  privacy-routing policy. Account-default models and the random
  `openrouter/free` router are rejected for decision-bearing review.
- A release may recommend an exact `:free` model only after that configuration
  passes the Mechanical Librarian cohort. Free price does not imply capability,
  availability, privacy suitability, or production eligibility.
- Free and paid variants have distinct immutable configuration and evaluation
  identities. Changing either never reuses prior qualification silently.
- The request requires declared structured-output parameters. Tools, plugins,
  web search, response healing, semantic repair, and undeclared transformations
  are disabled.
- The application validates the direct response independently of provider
  claims and rejects unknown fields, trailing content, partial or duplicate
  decisions, invented values, unknown citations, and identity mismatches.
- The active route satisfies its configured provider, retention, training, and
  regional constraints. If no compliant endpoint exists, the reviewer is
  unavailable rather than routed through a less-private endpoint.
- Requested and actual model and provider identities, routing policy, bounded
  usage, latency, and validation result are retained without prompt content,
  credentials, or reusable provider handles in operational diagnostics.
- Free-tier exhaustion, price changes, model retirement, or provider failure
  never selects another model, provider, paid route, or privacy policy unless
  the owner explicitly configured a separately qualified exact alternative.
- Authentication, credit, quota, rate-limit, provider, retirement, timeout,
  cancellation, malformed-output, and identity failures suspend only the
  affected reviewer configuration under bounded retry policy.
- Disabling or removing OpenRouter leaves historical provenance explainable
  and deterministic and human review operational.

### OpenRouter required qualification

The exact model and route selected when this slice is scheduled must pass the
shared semantic qualification plus provider or model identity mismatch,
material route drift, authentication, credit, quota, `402`, `404`, `429`,
timeout, provider `5xx`, cancellation, and retry-storm cases.

Qualification covers the disclosure and enablement state matrix: disabled with
a missing disclosure, disabled with a matching disclosure, and enabled with a
missing, stale, or wrong-configuration disclosure each block locally with zero
OpenRouter requests and zero credential transmission. Only enabled with a
currently matching accepted disclosure permits preflight or a reviewer
request. Material changes to the destination, model, provider, routing,
privacy, retention, training, region, free-or-paid status, cost or quota
behavior, sent-data, or attachment configuration invalidate acceptance and
block with the same zero-request and zero-credential result until the new
matching disclosure is accepted; unchanged requests do not require repeated
acceptance.

Any eligibility beyond shadow or manual review is granted to the exact
backend/model/provider/routing/prompt/schema/policy tuple, not to OpenRouter or
to free models generally. The cohort must expose systematic unsafe approvals
and define its acceptance and suspension thresholds before eligibility changes.

### Current external-service assumptions

OpenRouter currently documents bearer-key authentication, exact `:free` model
variants, a randomly selected free-model router, structured output on compatible
routes, provider data-policy controls, and variable free-tier limits. These are
external facts to verify when the slice starts, not Mechanical Librarian
guarantees:

- [authentication](https://openrouter.ai/docs/api/reference/authentication);
- [free variants](https://openrouter.ai/docs/guides/routing/model-variants/free);
- [free-model router](https://openrouter.ai/docs/guides/routing/routers/free-router);
- [structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs);
- [provider routing and data policy](https://openrouter.ai/docs/guides/routing/provider-selection); and
- [rate and usage limits](https://openrouter.ai/docs/api/reference/limits).

### OpenRouter failure behavior and test layers

Expected operational failures are typed and redacted, preserve attempt budgets
across restart, honor bounded server retry guidance, and never become negative
metadata evidence or reviewer decisions. Required test layers are unit and
schema tests, reviewer adapter contract tests, credential and redaction tests,
privacy-routing tests, persistence/restart tests, hostile-output tests,
synthetic cohort replay, and a disposable end-to-end hosted shadow run.

### OpenRouter delivery boundary

If promoted, one outcome should deliver configuration,
credential handling, the adapter, owner setup and status, immutable provenance,
failure handling, qualification fixtures, documentation, generated contracts,
changelog, and the risk-proportionate security review. It excludes generic
provider aggregation, anonymous project-funded service, automatic model
selection, silent fallback, production-write qualification, and unrelated
local-model support.

### OpenRouter promotion considerations

1. Decide whether a qualified route pins one provider or permits an allowlist
   of separately qualified providers for the same model. Actual provider
   identity remains mandatory either way.
2. Decide the default retention policy: mandatory zero-data-retention, or a
   no-training route with explicitly disclosed bounded retention. A less-private
   fallback cannot be implicit.
3. Decide whether the first setup surface accepts only a write-only key or also
   implements OAuth PKCE. Both must produce an owner-controlled reviewer secret.
4. Select the exact free and paid model/provider configurations only when the
   outcome is promoted because catalogue, capabilities, policies, and availability
   are time-sensitive.

### OpenRouter qualification constraints retained for promotion

If this outcome is promoted, its single concise outcome issue should incorporate
the material constraints below unless an independently valuable increment
justifies a split. They are promotion and qualification considerations, not a
pre-created constituent ticket or a Ready checklist while the outcome remains
deferred.

- Accept only an owner-controlled, reviewer-scoped credential and expose only
  its redacted status.
- Before every connection test, capability, identity, or authentication
  preflight, or reviewer request, require explicit enablement and a currently
  accepted immutable disclosure/configuration identity matching the active
  destination, model, provider, routing, privacy, retention, training, region,
  free-or-paid status, cost or quota behavior, sent-data, and attachment
  configuration; otherwise block locally with zero OpenRouter requests and
  zero credential transmission.
- Invalidate acceptance after any material change to that identity and require
  acceptance of the new matching disclosure before another request, without
  requiring repeated acceptance for unchanged requests.
- Select one exact qualified model and versioned provider/privacy route; reject
  account defaults, random routing, and implicit free-to-paid fallback.
- Use the fixed-origin bounded HTTPS reviewer client and never forward the
  credential outside its allowed origin.
- Preflight required schema, context, image, model, provider, and privacy
  capabilities before work becomes eligible.
- Validate one direct closed-schema response independently and reject malformed,
  incomplete, repaired, uncited, or identity-mismatched output.
- Persist immutable configuration, execution, requested/actual route, bounded
  usage, and validation provenance without secret or prompt disclosure.
- Create a new cohort for every material configuration change and pass the
  shared and OpenRouter-specific qualification cases.
- Convert authentication, credit, quota, retirement, rate, timeout, provider,
  cancellation, and validation failures into bounded typed states without
  switching routes.
- Demonstrate that deterministic and human workflows
  remain available when OpenRouter is disabled or unavailable.

A future promotion plan should define proportionate evidence including the
supported demonstration, adapter contract suite, privacy and credential checks,
hostile-output suite, replayable cohort report, and end-to-end shadow run.

## Deliverable B: Externally operated compatible reviewer

### External-server deferred outcome draft

**Title:** Deliver a reviewer backed by an externally operated compatible model
server

**Classification:** Deferred after-1.0-checkpoint Feature; High risk;
`Unassigned backlog`; no milestone or functional-slice designation

**Outcome:** An owner connects Mechanical Librarian to a separately installed
and operated model server, qualifies its exact endpoint/profile/model, and
receives validated recommendations without adding an inference runtime, model
weights, device integration, or sidecar requirement to the Mechanical
Librarian image.

**Dependencies:** F11, F17, the accepted reviewer-isolation decision,
structured reviewer endpoint and authentication configuration, and the
authenticated owner configuration surface.

### External-server supported demonstration

1. Start a disposable external compatible server or deterministic protocol
   fake independently of Mechanical Librarian.
2. Configure its exact origin, compatibility profile, model ID, optional
   reviewer-scoped credential, and explicit local-network policy.
3. Before accepting the destination-specific disclosure or enabling the
   endpoint, attempt a connection test, compatibility handshake, and
   capability, identity, and authentication preflight; prove each is blocked
   locally with zero external network requests and zero credential
   transmission.
4. Accept the matching destination-specific external-processing disclosure
   and explicitly enable the endpoint.
5. Preflight transport, direct structured output, context, image, model
   identity, authentication, and response-limit capabilities.
6. Run the synthetic F11 packet, validate the response, and inspect immutable
   endpoint/profile/model and validation provenance.
7. Restart Mechanical Librarian without managing or restarting the external
   server.
8. Inject DNS, TLS, authentication, redirect, private-address policy, model
   identity, capability, timeout, disconnect, overload, malformed-output, and
   server-version failures.
9. Confirm that disabling or losing the endpoint leaves the container healthy
   and deterministic and human review available.

### External-server acceptance criteria

- The Mechanical Librarian image contains no local inference runtime, model
  weights, model downloader, GPU dependency, or external-server lifecycle
  manager.
- Mechanical Librarian does not bundle, download, start, discover, upgrade, or
  manage the external runtime or its model weights.
- External inference remains optional owner-operated infrastructure and is
  never a normal deployment prerequisite or an automatically discovered
  service.
- No external endpoint request, including a connection test, compatibility
  handshake, capability, identity, or authentication preflight, or reviewer
  packet, occurs until the owner explicitly enables the endpoint and accepts
  the matching destination-specific disclosure. That disclosure names every
  bounded field and excerpt and every staged attachment or image category that
  may be sent, the processing purpose, configured destination, declared
  storage or retention posture, functionality lost when disabled, and
  synthetic connection-test path. It explicitly excludes raw media, whole
  works or audio, production paths, and unrelated evidence.
- Configuration names one exact `http` or `https` origin, compatibility
  profile, model ID, privacy mode, and optional reviewer-scoped secret. URLs
  containing credentials, fragments, ambiguous hosts, unsupported schemes, or
  unrestricted paths are rejected.
- Compatibility is proven by a bounded handshake and contract tests rather
  than inferred from the label "OpenAI-compatible". Preflight covers direct
  structured output, context and output limits, image support when declared,
  authentication, tokenizer or truncation behavior where observable, and
  actual model identity.
- The adapter uses a dedicated bounded client with no target or provider
  credentials, cookies, redirect state, arbitrary user headers, mutation
  authority, or shared target-service client state.
- HTTPS uses normal hostname and certificate validation. Private certificate
  authorities or certificate pinning may be configured through a reviewed
  bounded mechanism; there is no global TLS-disable option.
- Plain HTTP, loopback, or private-network access requires an explicit
  high-visibility local-endpoint mode restricted to the exact configured
  origin and this reviewer client. It grants no general private-network egress.
- Redirects are disabled by default. Any supported redirect is revalidated,
  remains inside the allowed origin policy, and never receives authentication
  after an origin change.
- DNS resolution and connection behavior prevent rebinding or destination
  changes from widening access. File, Unix-socket, link-local, multicast,
  reserved, metadata-service, and undeclared destinations fail closed.
- Optional endpoint authentication uses a reviewer-scoped bearer or file
  secret. Arbitrary headers, cookies, target credentials, and credentialed URLs
  are unsupported.
- Requested and actual endpoint, compatibility profile, server version, model
  identity, prompt, schema, policy, and configuration versions are persisted.
  Missing or changed identity suspends qualification and creates a new cohort.
- A text-only configuration is eligible only for explicitly declared text-only
  review categories. Image-dependent work remains unsupported or manual rather
  than dropping the attachment silently.
- Unavailability affects reviewer readiness only, not container liveness or
  deterministic and human workflows.

### External-server required qualification

Qualification independently covers the shared semantic cohort plus
endpoint-specific cases for:

- the disclosure and enablement matrix: missing disclosure plus disabled,
  accepted disclosure plus disabled, and enabled plus a missing, stale, or
  wrong-destination disclosure each block locally with zero external network
  requests and zero credential transmission; only an enabled endpoint with a
  matching accepted disclosure permits a connection test, compatibility
  handshake, capability, identity, or authentication preflight, or reviewer
  packet;
- invalidation of disclosure acceptance after any material destination,
  privacy, or configuration change, before another external request occurs;
- capability and model-identity mismatch;
- unsupported schema keywords and envelope differences;
- DNS rebinding, address-family changes, redirects, origin changes, and
  authentication-header leakage;
- certificate, hostname, private-CA, and optional pinned-certificate behavior;
- connection refusal, slow response, partial response, oversized response,
  disconnect, overload, restart, and retry storms; and
- server and model upgrades that preserve or break the compatibility profile.

Custom models begin shadow/manual-only. Any later eligibility is local to the
exact endpoint/profile/server/model/prompt/schema/policy tuple and requires its
own retained shadow evidence. Transport compatibility alone is never model
qualification.

### External-server failure behavior and test layers

Endpoint failures become typed, redacted reviewer states and never fail
container liveness, create decisions, or trigger another backend. Required test
layers are schema and configuration tests, adapter contract tests, SSRF/DNS/
redirect/TLS tests, credential isolation and redaction tests, capability and
identity tests, timeout/resource/restart tests, hostile-output replay, and a
disposable end-to-end external-server shadow run.

### External-server delivery boundary

If promoted, one outcome should deliver configuration, the
compatibility profile, the bounded network client, capability and identity
preflight, adapter and diagnostics, immutable provenance, qualification
fixtures, documentation, generated contracts, changelog, threat-model updates,
and independent safety review. It excludes model installation or updates,
server supervision, GPU/device support, network discovery, arbitrary API
compatibility, global private-network access, production-write qualification,
and OpenRouter support.

### External-server promotion considerations

1. Select the initial compatibility profile: a narrow Mechanical Librarian
   profile over OpenAI-style chat completions is recommended, with named
   runtime/version examples treated as qualified implementations rather than
   separate protocols.
2. Decide whether explicit plain HTTP to an exact private origin is supported;
   if so, record the threat analysis and high-visibility configuration UX.
3. Select the supported private CA or certificate-pinning mechanism without a
   global insecure switch.
4. Decide how exact origins remain stable across DHCP or local DNS changes
   without weakening destination validation.

### External-server qualification constraints retained for promotion

If this outcome is promoted, its single concise outcome issue should incorporate
the material constraints below unless an independently valuable increment
justifies a split. They are promotion and qualification considerations, not a
pre-created constituent ticket or a Ready checklist while the outcome remains
deferred.

- Keep inference runtime, weights, model lifecycle, device support, and service
  discovery outside the Mechanical Librarian image and process lifecycle.
- Require explicit owner enablement and acceptance of the matching endpoint-
  specific external-processing disclosure before any external request,
  including a connection test, compatibility handshake, capability, identity,
  or authentication preflight, or reviewer packet, can send network traffic or
  transmit a credential.
- Accept one exact origin, compatibility profile, model ID, privacy mode, and
  optional reviewer-scoped bearer or file secret.
- Restrict local/private-network authority to the exact configured origin and
  dedicated reviewer client without arbitrary headers, cookies, redirects, or
  shared target-service state.
- Preserve TLS verification and the reviewed private-CA or certificate-pinning
  behavior without a global insecure mode.
- Preflight direct structured output, context/output limits, declared image
  support, authentication, compatibility, and actual model/server identity.
- Validate one direct closed-schema response independently and reject malformed,
  incomplete, repaired, uncited, or identity-mismatched output.
- Persist immutable endpoint/profile/server/model/configuration and validation
  provenance and create a new cohort after material identity or version change.
- Pass the shared semantic cohort and endpoint-specific SSRF, DNS, redirect,
  TLS, credential, capability, timeout, overload, disconnect, and drift cases.
- Convert endpoint failures into bounded typed reviewer states and prove
  container liveness and deterministic and human workflows remain available.

A future promotion plan should define proportionate evidence including the
supported demonstration, adapter contract suite, network-boundary and
credential checks, hostile-output suite, replayable cohort report, and
disposable external-server shadow run.

## Planning disposition

The planning document is approved scope deferred until after the 1.0
checkpoint. The two outcome drafts are not implementation trackers. When one is
selected, create or update one concise outcome issue and explicitly promote
that same issue only after its named decisions are resolved, current
external-service behavior is reverified, and dependencies are complete. Add
constituents only for independently valuable increments justified by that
promotion plan.
