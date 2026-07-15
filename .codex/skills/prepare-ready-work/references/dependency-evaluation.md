# Dependency, library, and tool evaluation

Use this evaluation only when a framework, runtime or development library,
dependency, or developer tool is material to making the work implementable and
verifiable. A choice is material when it plausibly changes current effort,
maintenance or operational risk and also affects acceptance, durable
architecture, a trust or authority boundary, compatibility, runtime or
container footprint, repository-wide contributor workflow, or the delivery
boundary.

Do not trigger merely because a dependency is new, a standard-library option
exists, or an implementation worker will need to select routine packages.

## Evaluate proportionately

1. State the current requirement and the no-new-dependency or existing-tool
   baseline.
2. Compare only the smallest credible set, normally the baseline plus one to
   three candidates. Omit clearly unsuitable options rather than building a
   market survey.
3. Verify changeable facts from current primary sources. Record the version or
   release line and evidence date when relevant.
4. Assess only material differences in:
   - fit for the current requirement and expected reduction in effort,
     maintenance burden, or operational risk;
   - runtime versus development-only classification and meaningful transitive,
     image, build, or installation impact;
   - maintenance and security history, licence and provenance, required
     notices, and compatibility with `AGPL-3.0-or-later`;
   - network, credential, filesystem, private-data, subprocess, or production-
     write authority;
   - supported-version compatibility, reproducible pinning and locking, and
     the behavior and failure seams the delivery must test; and
   - reversibility, substitution, or removal cost.
5. Use a bounded local spike or benchmark only when it is the cheapest safe way
   to resolve a material uncertainty. Never use production credentials, private
   library data, or a live target.

## Conclude without creating ceremony

Choose one result:

- recommend one candidate inside the current ticket or slice;
- leave selection to the delivery worker with explicit constraints when the
  options do not change acceptance or governing boundaries;
- return `NEEDS MAINTAINER DECISION` when a consequential choice changes
  durable architecture, a public contract, trust or authority, compatibility,
  or the delivery boundary; or
- return `BLOCKED` when required evidence or an external prerequisite is not
  obtainable.

Require an ADR or safety review only when existing repository policy requires
it. Use separate focused work only when the dependency, upgrade, removal, or
repository-wide tooling change is itself the primary outcome or must land
independently. Do not create a horizontal permission ticket.

## Packet evidence

Add a short optional subsection containing:

- why the evaluation triggered;
- the baseline and credible candidates;
- the recommendation and decisive evidence;
- delivery constraints and implementation-time revalidation; and
- the exact unresolved maintainer decision, if any.

Use a table only when it makes two or more candidates easier to compare.
