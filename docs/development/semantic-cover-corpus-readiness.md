# Semantic cover corpus readiness — 0.15.0

Status: **PASS** (2026-08-11)

This aggregate-only record supersedes the prior insufficiency report for the
best-effort provider-cover qualification proof. It deliberately excludes corpus
and candidate identities, metadata, provider values and payloads, media,
labels, paths, filenames, identifiers, URLs, digests, credentials, raw
responses, and per-case outcomes.

## Frozen scope and boundaries

- The proof uses real source works through a disposable loopback read-only
  target and the existing report, provider, and cover-artifact seams.
- Only configured Hardcover and Open Library cover lanes were used; Google
  Books remained metadata-only. Request, retry, pacing, truncation, gallery,
  decoder, and network bounds were not widened.
- There was no production-library access, target or media write, provider
  envelope change, model/reviewer dispatch during acquisition, or runtime
  activation.
- The final-holdout cohort remains adjudication-only and unavailable to
  implementation and tuning. Source, work-family, edition/ISBN-family, and
  near-duplicate split checks passed.

## Recoverable inventory

| Measure | Aggregate result |
| --- | ---: |
| Frozen source works | 340 |
| Completed report runs | 339 |
| Terminal report failures | 1 |
| Retries | 0 |
| Unreadable shared artifacts excluded | 1 |
| Distinct verified usable provider previews | 597 |
| Work families with usable provider previews | 315 |
| Work-family-disjoint multi-choice packets | 224 |
| Packets with at least five usable previews | 26 |

The current-schema report store and content-addressed artifacts passed a fresh
read, integrity, foreign-key, and digest check. The inventory therefore meets
the minimum proof thresholds for source works, usable previews, work families,
multi-choice packets, and five-preview packets.

Historical qualification-role packet provenance and exact historical
wheel/configuration provenance are unavailable. Under #546's
minimum-proof clarification, they are reported as unavailable and are not
claimed as PASS evidence.

Provider cover outcomes are bounded aggregate observations, not semantic
quality measurements:

| Provider | Success | No result | Failure | Truncation |
| --- | ---: | ---: | ---: | ---: |
| Hardcover | 661 | 7 | 20 | 10 |
| Open Library | 76 | 207 | 348 | 12 |

Truncation is an overlapping bounded outcome, not an additional terminal
category.

## Identity-review qualification

One recorded random seed selected exactly 100 candidate-to-target comparisons:
50 development and 50 final-holdout cases. The selected cases are
source- and work-family-disjoint. All 100 have a first source-first review, an
independent second packet-local review, and evidence-only final adjudication.
There were 45 agreements and 55 disagreements; adjudication retained the
first-review class after policy-and-evidence reconciliation.

| Closed identity class | Development | Final holdout | Both cohorts |
| --- | ---: | ---: | ---: |
| Verified manifestation match | 20 | 25 | 45 |
| Verified work match | 12 | 9 | 21 |
| Plausible match | 11 | 11 | 22 |
| Unknown | 4 | 3 | 7 |
| Conflict | 3 | 2 | 5 |

This is the natural observed class distribution. It is not a statistical or
model-quality claim, and no case was selected, moved, or relabelled to improve
that distribution.

## Outcome

Issue #546 passes as a best-effort general-principle proof of a bounded,
aggregate-safe provider-cover corpus. It establishes neither semantic quality
nor runtime activation, and it does not qualify a model or a later transport
unit. The tracked report contains no private corpus material.
