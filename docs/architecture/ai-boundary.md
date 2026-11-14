# AI trust boundary

An AI model is untrusted
([ADR-0004](decisions/0004-ai-is-untrusted.md)). It never sees a target or
provider credential, never chooses its own inputs, never changes policy,
never builds a service request and never decides or writes a field. Its
output is either advisory evidence or a stored text candidate. Local code
validates it, and only an owner decision or a qualified automatic decision
can use it, through the ordinary guarded write path. Without a reviewer the
manual path is complete.

Deterministic decisions sit outside this boundary. Policy preview, shadow
decisions and automatic decisions make no model or provider call.

## Where models are used

The owner selects a reviewer per task in Settings → Reviewer: the contained
Codex reviewer or an OpenRouter model. Reviewer access is separate authority,
and changing it makes current advice stale while keeping its history
([ADR-0062](decisions/0062-owner-managed-reviewer-access.md)). Setup is in
[deployment](../operations/deployment.md#optional-reviewers).

**Cover observation.** A reviewer looks at report-bound cover previews and
returns a fixed set of visual properties with explicit unknowns, never a
ranking, winner or identity. Identity classification, policy and the owner's
choice stay local, and a missing or failed observation leaves the manual
choice available
([ADR-0049](decisions/0049-owner-confirmed-cover-considerations.md),
[ADR-0050](decisions/0050-bound-installed-owner-assistance.md)).

**Description advice.** A reviewer rewrites the deduplicated provider-backed
descriptions into one description, under the owner's three cleanup choices.
It never receives owner decisions, target-only text, titles, identifiers,
paths or credentials. The reply is one bounded description and `ready` or
`needs_review`, stored as authored content. The owner can select a stored
result by reference. Automatic application is off by default; when the owner
turns it on, only a current `ready` result that passes the description floor
can be admitted, through the normal policy and write rules
([configuration](../reference/configuration.md#description-advice)).

**Taste profile.** An OpenRouter vision model writes the owner's taste
profile from up to 50 of their recorded cover choices: up to 100 cover images
and closed facts about each (size, shape, same-art group, chosen or passed
over, current, and the book's format), never a title, author, identifier,
path or provider text. The reply is up to 40 numbered rules in four sections,
stored as an append-only version and shown read-only in Covers → Taste
profile. It influences nothing yet: no observation, recommendation or
decision reads it (#1372). Builds are scheduled, at most 4 a day, and every
request counts against a daily limit
([configuration](../reference/configuration.md#taste-profile)). The task is
OpenRouter only in 0.31.0
([ADR-0095](decisions/0095-learned-cover-taste-profile.md)).

## Request contract

Every request, on either engine, has three parts:

- a pinned, bounded instruction;
- one canonical, sanitised JSON input document of at most 1 MiB;
- a closed output schema of at most 64 KiB from the locally admitted JSON
  Schema subset.

The reply must be exactly one canonical JSON object that validates locally
against that schema; otherwise it is a closed, value-free failure. A
provider's or CLI's structured-output enforcement is never the only check.
Packet text, provider text and images are data, never instructions. Raw
streams, prompts, private paths, credentials and model reasoning never cross
back into the product. A failed observation, description attempt or taste
profile build is retried only on an explicit owner retry.

## Taste profile builder

The taste profile builder (#1372) is OpenRouter only in 0.31.0, with its own
model selection under the shared key
([ADR-0095](decisions/0095-learned-cover-taste-profile.md)). It reads owner
cover choices already recorded and writes only its own append-only profile
versions; nothing reads a profile yet, so it cannot influence an observation,
recommendation, decision or write.

- **Inputs.** Up to 50 recorded choices, each with up to 4 previews and at
  most 100 images per request. A choice whose preview bytes are missing is
  dropped, never sent partly.
- **Closed facts per image.** The original's decoded width and height (each
  1–8,192, area at most 16,777,216); shape `fits` or `wrong_shape` against the
  medium; an opaque same-art group letter; role `chosen` or `passed_over`; a
  current marker. Each choice adds its medium, `ebook` or `audiobook`, the one
  closed value this adds to what a model may receive. No title, author,
  series, identifier, path, URL, provider text or stated dimension is sent.
- **Transport.** Each normalised preview PNG is flattened on white and
  encoded as a deterministic JPEG at quality 90, sealed in the packet by
  digest and re-derived and compared byte for byte before sending. The
  request is at most 24 MB. The cover observer keeps the PNG.
- **Output.** A closed schema: at most 40 rules in four sections, each at
  most 400 code points, at most 1,200 words and 8,192 code points in all,
  admitted as inert text and only ever rendered as text.
- **Ceilings.** One build at a time and at most 4 scheduled a UTC day. Each
  request is counted durably before dispatch against
  `cover_taste_build_daily_requests`; a timeout after sending or a reply
  without usage stops the task for the day as `unknown_charge`. A build
  interrupted by restart ends `interrupted` and is never retried silently.
- **Storage.** Versions are append-only by trigger, and each stored profile
  is re-admitted against its document when the database opens.
- **Redaction.** Failures are closed codes. Rule text, choices, references,
  previews, prompts and raw output never reach a log, failure history,
  diagnostic, status or support bundle.

## Engines

The contained Codex reviewer runs in a sandbox its profile pins
([ADR-0081](decisions/0081-image-owned-reviewer-provisioning.md)); the child
sees one empty job directory, a read-only credential and the request.
OpenRouter egress goes only to one exact model. The mechanics are in
security: [Codex](security.md#contained-codex-reviewer) and
[OpenRouter](security.md#openrouter).

## Contributor workbench

The [workbench](../development/contributor-workbench.md) uses the same
`contained-reviewer` adapter for experiments and persists no product data.

The current reviewer design is
[ADR-0041](decisions/0041-contract-contained-reviewer-kernel.md).
