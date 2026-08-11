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
  Books remained metadata-only. The retained record does not establish the
  exact acquisition-time request, retry, pacing, truncation, gallery, decoder,
  or network bounds, so this report makes no claim about those bounds.
- There was no production-library access, target or media write,
  model/reviewer dispatch during acquisition, or runtime activation.
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
read, integrity, foreign-key, and digest check. The durable best-effort minimums
and observed results are:

| Proof gate | Minimum | Observed | Result |
| --- | ---: | ---: | --- |
| Admitted real source works | 120 | 340 | PASS |
| Distinct usable report-bound provider previews | 300 | 597 | PASS |
| Work families represented by usable previews | 75 | 315 | PASS |
| Work-family-disjoint multi-choice packets | 60 | 224 | PASS |
| Inventory packets with at least five usable previews | 10 | 26 | PASS |
| Random candidate-to-target identity cases | 100 (50 development + 50 final holdout) | 100 (50 + 50) | PASS |

The identity proof additionally requires the same 100 cases to receive
independent first and second reviews plus evidence-only adjudication, with no
identity-class quota. That review gate is recorded below.

Historical qualification-role packet provenance, exact historical
wheel/configuration provenance, and acquisition-time limit provenance are
unavailable. They are not claimed as PASS evidence. This PASS is limited to the
versioned minimums above and the verified current-schema report, artifact,
split, review, and adjudication evidence.

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

## Identity-classifier qualification — PASS

The source-only classifier was run against the unchanged 50-case development
cohort before the final holdout was opened. Forty-nine cases had an exact
report-bound cover source; one retained case was typed unavailable instead of
being replaced. After the implementation and its exact artifact passed review
and validation, the unchanged 50-case final holdout was opened once.

| Measure | Development | Final holdout | Combined |
| --- | ---: | ---: | ---: |
| Verified manifestation predictions | 20 | 25 | 45 |
| Verified work predictions | 12 | 9 | 21 |
| Plausible predictions | 0 | 0 | 0 |
| Unknown predictions | 14 | 14 | 28 |
| Conflict predictions | 3 | 2 | 5 |
| Typed unavailable cases | 1 | 0 | 1 |
| Exact matches / evaluable cases | 39/49 | 39/50 | 78/99 |
| Verified precision | 32/32 | 34/34 | 66/66 |
| Reviewed conflicts retained | 3/3 | 2/2 | 5/5 |

Ten development and eleven final reviewed `plausible` cases were
conservatively projected as `unknown` because the retained report evidence was
ambiguous; the remaining reviewed plausible development case was the typed
unavailable case. All reviewed unknown cases remained unknown. This is an
honest precision-first negative recall result, not a semantic-quality or
production-activation claim. No case was tuned, moved, relabelled, or replaced.

Both runs made zero provider or model calls and performed zero production
access or writes. The final holdout was unavailable to implementation and
tuning before its one-shot aggregate qualification.

## Outcome

Issue #546 passes as a best-effort general-principle proof of a bounded,
aggregate-safe provider-cover corpus. It establishes neither semantic quality
nor runtime activation, and it does not qualify a model or a later transport
unit. The tracked report contains no private corpus material.
