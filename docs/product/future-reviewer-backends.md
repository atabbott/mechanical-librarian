# Future reviewer backends

The backends on this page do not exist yet. They are a direction for later,
and neither is scheduled or approved for work.

## What exists today

Today a reviewer gives advice on two jobs only. The cover reviewer looks at
covers (`cover_observation`), and the description adviser suggests a
rewritten description (`description_rewrite`). For each, you can choose Codex
or one OpenRouter model
([ADR-0062](../architecture/decisions/0062-owner-managed-reviewer-access.md)).
The installed Codex reviewer runs inside the contained-reviewer kernel, an
isolated sandbox. Every other use of the kernel stays in the source code and
is not installed
([ADR-0041](../architecture/decisions/0041-contract-contained-reviewer-kernel.md),
[ADR-0050](../architecture/decisions/0050-bound-installed-owner-assistance.md)).
None of this is a general reviewer backend or settles the interface a future
backend would use.

## Direction

After 1.0, two optional backends are possible. Neither depends on the other.

1. An OpenRouter reviewer for more jobs than today's two. It uses your own
   OpenRouter account and one exact, qualified model configuration, free or
   paid.
2. A compatible model server that you install and run yourself, outside
   Mechanical Librarian.

Before work starts on either, it needs a new GitHub issue that schedules it, a
product reviewer contract and a trust-boundary decision. It also needs a
design for its configuration, privacy and secret references. How the outside
service behaves is checked again at that point.

## Rules for every backend

Every backend connects through a new interface that Mechanical Librarian
defines, and stays inside the
[AI trust boundary](../architecture/ai-boundary.md). For each backend:

- It receives only a size-limited review request in a versioned format, and
  the attachments Mechanical Librarian picked and prepared for it.
- It replies with one object in a fixed schema, and nothing else. The object
  covers every proposal it was given, and no other.
- It gets no credential for your library service or a source. It cannot reach
  the main database, backups or your media files. It has no shell, no tools
  and no way to write or apply a change.
- It cannot choose the values it judges, the evidence, the policy, its
  prompts, the fields to change or what is written.
- Before it is given any work, it declares what it supports: text, images,
  the longest input it accepts, schema, authentication and model identity.
- It reports the model, endpoint and model provider that answered. Mechanical
  Librarian keeps them in a record that never changes, with the versions of
  the prompt, evidence, schema, policy and configuration.
- It has hard limits on input, output, response size, time, parallel requests
  and retries, and a cut-off that stops requests after repeated failures.
- Each failure becomes a named state: authentication, quota, availability,
  capability, identity, transport, timeout, malformed output or citation. It
  never becomes a guessed or repaired decision. A failure pauses only the
  configuration it affects, with limited retries, and is never evidence about
  a field.

A change of endpoint, provider, model, free or paid status, or privacy policy
is a configuration change you make, and the audit record shows it. A backend
never changes any of these by itself.

Qualification never carries over. A change to the identity a backend reports,
or to anything material in its configuration, starts a new evaluation. That
identity is the model, endpoint and model provider that answered, plus the
server version for a model server. Shadow review records a backend's answers
without using them. In manual review, you decide. Only one exact combination
of backend, model, provider, route, prompt, schema and policy can qualify for
more than that. A vendor, or free models in general, never qualify.

Mechanical Librarian checks every answer itself, whatever the provider claims.
It rejects unknown fields, trailing content, partial or duplicate decisions,
invented values, unknown citations and a mismatched identity. Tools, plugins,
web search and response repair stay off.

Nothing goes out until you turn the backend on and accept a disclosure that
matches its current configuration: no connection test, preflight check,
review request or credential. The disclosure names every field, excerpt and
kind of attachment sent, who processes them, and the retention, training,
region and cost terms. It says that raw media, whole works, file paths in
your library and unrelated evidence are never sent. After a material change,
you accept again.

You control the credentials. Each is a write-only reference to a secret that
only the reviewer can use. The browser never shows a saved credential.
Credentials never appear in exports, logs, the audit record or support bundles.
The image ships with no shared credential.

Each backend has its own network client, limited to its configured origin. It
checks TLS certificates as normal and never follows a redirect that would
carry a credential. It shares no state with the clients for your library
service or sources.

Inventory, local evidence, rule-based proposals and policy, and your own
review keep working when no reviewer is set up or every reviewer is failing.
A reviewer failure never makes the container unhealthy.

The [data-privacy contract](../architecture/data-privacy.md) applies to any
processing outside Mechanical Librarian.

## Rules by backend

An OpenRouter reviewer never uses your account's default model or the random
`openrouter/free` router for a review that informs a decision. If no route
meets the configured provider, retention, training and region limits, the
reviewer is unavailable. It never falls back to a less private route. Running
out of free use, a price change or a retired model never selects another
model, provider or paid route.

A model server is yours to run. The image carries no inference runtime, model
weights, downloader, GPU dependency or server lifecycle manager. Mechanical
Librarian never discovers, starts or upgrades the server. Its configuration
names one exact `http` or `https` origin, compatibility profile, model ID and
privacy setting, and an optional secret for the reviewer. Mechanical Librarian
refuses a URL with a credential, a fragment, an ambiguous host, another scheme
or an unrestricted path. The reviewer signs in only with a bearer token or a
secret file, never with custom headers or cookies.

A handshake with fixed limits proves the server is compatible. Mechanical
Librarian does not take an "OpenAI-compatible" label as proof. Plain HTTP,
loopback or a private network needs a visible local-endpoint setting that you
turn on, limited to that origin. Mechanical Librarian blocks DNS rebinding,
redirects, and file, socket, link-local, metadata-service or undeclared
destinations. No setting turns TLS checking off globally. A custom model starts
in shadow or manual review only. A text-only configuration leaves any work
that needs images to manual review.
