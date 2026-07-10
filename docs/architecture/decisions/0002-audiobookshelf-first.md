# ADR-0002: Build the First Adapter for Audiobookshelf

- Status: accepted
- Date: 2026-07-10
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Audiobookshelf can present ebooks and audiobooks in one service and exposes
supported metadata and cover APIs. This provides a coherent first production
target while the domain remains capable of supporting other services later.

## Decision

The first stable integration targets Audiobookshelf book libraries for ebooks
and audiobooks. Audiobookshelf-specific fields, payloads, events, and
capabilities stay inside its adapter.

Core domain names must not use `abs` or assume audiobook-only behavior.

## Consequences

### Positive

- One adapter proves both ebook and audiobook workflows.
- Supported APIs avoid direct database writes.
- Capability-detected notifications can reduce latency while polling remains a
  reliable reconciliation path.

### Negative

- Early fixtures and operational evidence may overrepresent Audiobookshelf.
- Service API changes require compatibility detection and contract maintenance.

### Neutral or deferred

- Calibre-Web/Calibre support remains a later adapter and cannot distort the
  initial core.

## Validation

- The domain test suite runs without importing the Audiobookshelf adapter.
- A fake service passes inventory, apply, verification, optional-notification,
  polling, and failure contract tests.
- Unsupported service fields are reported, never applied through fallback DB
  or UI automation.
