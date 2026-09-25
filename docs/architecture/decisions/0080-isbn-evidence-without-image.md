# ADR-0080: Verify ebook ISBN identity independently of image retrieval

Status: Accepted

## Context

A downloaded cover image is transport evidence, not additional bibliographic
identity. Requiring it prevents useful ISBN election when exact accepted edition
provenance is retained but acquisition failed (#1077).

## Decision

Remove only the retrieved-image prerequisite from empty ebook ISBN election.
Retain valid edition-scoped ebook claims, exact accepted provider/candidate/work
and reference binding, resolved work, unrelated-evidence refusal, publication
compatibility, and one verified ISBN family. Keep the existing single-caller
identity helper as the directly testable boundary. Do not change cover authority.

## Consequences

Policy v32 and successor compact qualification bindings require a clean rebuild.
Old outcomes are not reinterpreted; report and shadow-outcome schemas stay fixed.
Representation options, populated-value preservation, Ask/Ignore, owner-final,
consent, backup, currentness, optimistic concurrency, read-back and audit remain.
No provider call or new write capability is introduced. Bounded exposed-development
qualification and synthetic persisted/guarded-effect proofs validate the change;
no new holdout or population accuracy claim is made.
