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
and validation, the unchanged 50-case final holdout was opened. The later C1
connector review required three structural correctness fixes and therefore
invalidated the exact head; one blind aggregate requalification was performed
under the milestone rule to rerun invalidated evidence. It exposed no identity,
per-case result, or tuning signal, and every aggregate result remained
unchanged.

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
tuning before qualification and remained unavailable during the blind
post-review requalification.

## Cover-observation qualification — bounded contract PASS

After the observation contract and taxonomy binding passed independent code
review, one recorded random seed selected 100 real report-bound preview
candidates: 50 development and 50 final holdout. The sample contains 100
distinct reports and 100 distinct selected preview digests. No case was
selected by label, and the final holdout remained sealed until the development
admission replay was complete.

| Measure | Development | Final holdout | Combined |
| --- | ---: | ---: | ---: |
| Randomly selected cases | 50 | 50 | 100 |
| Exact packet/image/request admissions | 50 | 50 | 100 |
| Binding or mapping mismatches | 0 | 0 | 0 |
| Shuffled deterministic repeat admissions | 20/20 | — | 20/20 |
| Contained observer dispatches | 0 | 0 | 0 |
| Admitted semantic observation results | 0 | 0 | 0 |

The real admission replay proves the closed instruction, taxonomy, output
schema, packet, ordered-image, candidate, and byte bindings on the retained
corpus. The final holdout caused no implementation or tuning change.

The contained semantic observer **did not execute**. The inherited #542
qualification result remains `contained_reviewer_invocation_insufficient`, so
natural label counts, semantic repeat agreement, and semantic quality are not
available. Historical #542 latency cannot be attributed to this head. This is
an honest general-principle PASS for the deterministic typed contract and real
admission boundary, not a model-quality result or runtime activation. No retry,
profile, model, authority, limit, threshold, provider, or corpus change was
used to manufacture a semantic result.

## Cover-policy qualification — deterministic contract PASS

After the deterministic adjudicator passed independent code review, it replayed
the unchanged random 100-packet cohort from the observation proof: 50
development and 50 final holdout. Each packet was re-admitted with its exact
images and locally derived identity report. All eight combinations of the three
closed policy booleans were applied to every packet.

| Measure | Aggregate result |
| --- | ---: |
| Frozen real packets admitted | 100/100 |
| Closed policies per packet | 8 |
| Deterministic decisions | 800 |
| Binding or mapping mismatches | 0 |
| Typed `observations_unavailable` abstentions | 800 |
| Recommendations | 0 |
| Policy or eligibility violations | 0 |
| Invented references | 0 |
| Model or provider dispatches | 0 |

This all-abstention result is the required honest real outcome: #541 produced
no admitted semantic observations, so #543 did not fabricate labels or force a
choice. Exhaustive synthetic closed-vocabulary tests separately prove exact
acceptable sets, policy exclusions, minimal preference, combined-policy
precedence, ambiguity and every typed abstention/failure boundary. The final
cohort caused no implementation or tuning change.

The deterministic policy contract therefore passes, while acceptable-set
adherence and semantic policy quality remain **not established**. No second
reviewer, retry, provider, corpus, threshold, profile, model, limit or authority
change was used to manufacture a recommendation.

## Outcome

Issue #546 passes as a best-effort general-principle proof of a bounded,
aggregate-safe provider-cover corpus. It establishes neither semantic quality
nor runtime activation, and it does not qualify a model or a later transport
unit. Issue #541 separately passes its bounded typed observation-contract and
real-admission proof while preserving the negative semantic-execution result.
Issue #543 passes its deterministic policy and real fail-closed abstention
proof while making no semantic-quality or recommendation claim.
The tracked report contains no private corpus material, and #501 remains
unpromoted.
