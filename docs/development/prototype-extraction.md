# Prototype Extraction Policy

## Purpose

The earlier Ebook Management workspace proved workflows and contains useful
logic, but it is not the architectural base of Mechanical Librarian. Extraction is
selective and evidence-driven.

## Rule

Do not copy a prototype module merely because it works in one environment.
First identify the behavior worth preserving, encode that behavior in portable
fixtures and tests, then decide whether to extract, adapt, or reimplement.

## Extraction checklist

A component may be extracted only when:

- its licence and authorship are compatible with this repository's licence;
- it contains no personal paths, domains, identifiers, reports, or credentials;
- its public contract is narrow and documented;
- service-specific behavior is behind the correct adapter boundary;
- filesystem and environment assumptions are configurable;
- report-only and write behavior are explicit;
- errors are typed rather than inferred from printed output;
- it has isolated unit and failure tests;
- it meets current typing, linting, security, and documentation gates;
- copied history or attribution is preserved where required.

## Prefer extraction for

- pure normalization and comparison functions;
- bounded EPUB and audio evidence parsing;
- cover dimension and quality primitives;
- provider response normalization;
- Audiobookshelf API payload and verification knowledge;
- review-plan validation rules;
- fingerprints, idempotency concepts, and failure vocabularies;
- sanitized structural fixtures.

## Prefer reimplementation for

- large workflow scripts with mixed concerns;
- hard-coded local paths and environment discovery;
- static dashboards and browser-local workflow state;
- console-output parsing between stages;
- direct database snapshot assumptions;
- code coupling CWA and Audiobookshelf throughout the core;
- human approval gates embedded into candidate generation;
- code without clear provenance or safe reusable fixtures.

## Extraction record

Each extracted component should have a short record in its pull request:

- prototype source and revision;
- preserved behavior;
- rejected prototype behavior;
- new package and contract;
- fixture sanitization statement;
- licence and attribution conclusion;
- tests proving parity and new safety behavior.

## Runtime independence

The production project must build, test, install, migrate, and operate with no
checkout, import, volume, state file, or runtime call into the prototype
workspace.
