# Semantic cover corpus readiness — 0.15.0

Status: **PASS** (2026-08-12 cumulative review package)

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

The #999 follow-up regenerated all 50 retained qualification packets from the
immutable archived reports and artifacts after correcting incumbent duplicate
dimensions. Packet bindings, omissions, references, transport digests and
independent identity/order/readiness expectations remained within the frozen
qualification contract. This is retained regression evidence, not a new
holdout or an acquisition-quality claim; the archive, scorer, selection and
adjudication inputs remained immutable.

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

The corrected source-only classifier was evaluated on 99 usable retained cases;
one unreadable preview remained unavailable and was not replaced. Exact
agreement was 96/99 (96.97%): all 45 verified-manifestation, 21 verified-work,
seven unknown, and five conflict references were exact. Eighteen of 21
plausible references were recovered and three remained conservatively unknown.
There were zero false plausible or false verified results and no deterministic
conflict was lost.

The exact-current packet replay then admitted 79 packets and all 232 candidates
with zero binding failures. Its outcomes were 117 verified manifestation, 81
verified work, 31 plausible, two unknown, and one conflict. Exact report-bound
source covers prove only their own manifestation; provider discovery evidence
can support plausibility but cannot manufacture verified identity. Missing
creator evidence remains neutral and exact retained creator overlap can support
a match without fuzzy author inference. Neither replay made a provider or model
call or performed production access or writes.

## Contained cover transport — PASS

The historical contained-reviewer attempt failed before reviewer execution.
Diagnosis isolated two independent local causes: sealed image `/proc` path
mounts were not resolvable by Bubblewrap, and an authentication-capable private
profile was too small for inference. The corrected adapter uses verified
child-private descriptor cursors as descriptor-data mounts. A no-model
five-image Linux proof reproduced the exact bytes inside the sandbox while the
parent repeatedly sought its inherited descriptors to EOF, and a bounded
512 MiB memory and 32-task isolated profile completed a structured reviewer
probe.

The corrected exact implementation then admitted one retained real development
packet with five exact previews. It returned five ordered observations with zero
reference mismatch in 16.263 seconds and cleaned the ephemeral job. This passes
the issue's five-candidate, zero-mismatch, p95-at-most-180-second transport bound.
It is not yet the 100-case semantic-quality or repeatability result below. No
provider call, corpus rebuild, final-holdout exposure, production access, write,
runtime activation, model change, or threshold reduction was used.

## Cover-observation qualification — semantic proof PASS

Independent blinded reference judgements were frozen before tested observer
outputs were opened. The final reference judgements were independently reviewed
twice and adjudicated. These are review judgements, not claimed human ground
truth. Every replacement cohort was work-family-disjoint from the development
cohort and the other final cohorts. Public evidence contains aggregates only.

Every cohort design sealed a deterministic seed before selection or viewing.
Selection did not inspect semantic reference judgements or tested observer
outputs. Each replacement excluded every previously used source, work, report,
preview, and near-duplicate family; seed values and case identities remain
private.

| Frozen cohort | Multi-choice packets |
| --- | ---: |
| Development | 46 |
| Original final, invalidated before execution | 37 |
| Replacement final, spent at setup | 45 |
| Recovery final, one shot | 25 across 29 fresh packets |

The single development hypothesis was that the existing sealed-image packaging,
fixed instruction, closed schema, and corrected bounded profile would admit the
sample and reach at least 75% exact agreement for every field with stable
repeats. The first configuration passed, so the permitted second and third
configurations were not run.

| Development measure | Aggregate result |
| --- | ---: |
| Candidates admitted | 50/50 |
| Composition exact agreement | 43/50 (86%) |
| Medium exact agreement | 42/50 (84%) |
| Promotional-text exact agreement | 46/50 (92%) |
| Tie-in exact agreement | 49/50 (98%) |
| Seeded repeat, each field and joint | 20/20 (100%) |
| Initial p50 / p95 latency | 9.76 s / 13.73 s |
| Repeat p50 / p95 latency | 8.95 s / 12.22 s |
| Failures / retries / violations | 0 / 0 / 0 |

Development aggregate confusion was:

| Field | Reference → observer | Count |
| --- | --- | ---: |
| Composition | `dense` → `dense` / `minimal` | 32 / 3 |
| Composition | `minimal` → `dense` / `minimal` | 4 / 11 |
| Medium | `illustrated` → `illustrated` / `typographic` | 19 / 1 |
| Medium | `mixed` → `mixed` | 1 |
| Medium | `photographic` → `illustrated` / `photographic` | 7 / 15 |
| Medium | `typographic` → `typographic` | 7 |
| Promotional text | `absent` → `absent` / `present` / `unknown` | 16 / 2 / 1 |
| Promotional text | `present` → `present` | 30 |
| Promotional text | `unknown` → `present` | 1 |
| Tie-in branding | `absent` → `absent` | 48 |
| Tie-in branding | `present` → `present` | 1 |
| Tie-in branding | `unknown` → `absent` | 1 |

Observer `unknown` predictions were 0/50 for composition, 0/50 for medium,
1/50 (2%) for promotional text, and 0/50 for tie-in branding.
Development minimal-positive precision was 11/14 (78.57%) and
reference-minimal recall was 11/15 (73.33%). The precision is in the mixed band,
the recall is below the useful band, and 15 reference-minimal cases are sparse;
development therefore did not establish prefer-minimal policy usefulness.
Tie-in branded-positive recall was 1/1 and absent-case specificity was 48/48;
the single positive is too sparse to support a strong detection claim. The one
reference-unknown case was predicted absent and is excluded from both rates.

One frozen final cohort was invalidated before execution because a broad private
JSON discovery scanner opened final-reference files, although no reference
values entered tested or model-visible output. Its fresh disjoint replacement
was spent during setup with 0/50 cases run because a credential file was mounted
at a directory target. Diagnosis corrected the preflight; neither attempt
supplied semantic evidence. No prompt, model, schema, packaging, taxonomy,
threshold, provider, corpus, network authority, or resource envelope changed.

The next fresh disjoint final cohort passed an explicit preflight of the
credential directory and file, lock, exact artifacts and version,
authentication state, job root, namespace and sealed transport, and exact
source/configuration/media/binding boundaries before its one shot.

| Final-holdout measure | Aggregate result |
| --- | ---: |
| Candidate rows admitted | 48/50 (96%) |
| Candidate-scoped packet invocations / failed invocations / retries | 50 / 2 / 0 |
| Distinct selected packets / multi-choice packets | 29 / 25 |
| Distinct successful / failed packet counts | unavailable / unavailable |
| Composition exact agreement | 40/48 (83.33%) |
| Medium exact agreement | 47/48 (97.92%) |
| Promotional-text exact agreement | 45/48 (93.75%) |
| Promotional-text unknown predictions | 2 |
| Tie-in exact agreement | 46/48 (95.83%) |
| All-four exact agreement | 36/48 (75%) |
| p50 / p95 latency | 10.74 s / 14.83 s |
| Safety or binding violations | 0 |

Final-holdout aggregate confusion among the 48 admitted candidate rows was:

| Field | Reference → observer | Count |
| --- | --- | ---: |
| Composition | `dense` → `dense` / `minimal` | 25 / 6 |
| Composition | `minimal` → `dense` / `minimal` | 2 / 15 |
| Medium | `illustrated` → `illustrated` / `typographic` | 25 / 1 |
| Medium | `mixed` → `mixed` | 1 |
| Medium | `photographic` → `photographic` | 9 |
| Medium | `typographic` → `typographic` | 12 |
| Promotional text | `absent` → `absent` / `present` / `unknown` | 15 / 2 / 1 |
| Promotional text | `present` → `present` | 29 |
| Promotional text | `unknown` → `unknown` | 1 |
| Tie-in branding | `absent` → `absent` | 44 |
| Tie-in branding | `present` → `absent` / `present` | 2 / 2 |

Observer `unknown` predictions were 0/48 for composition, 0/48 for medium,
2/48 (4.17%) for promotional text, and 0/48 for tie-in branding.
Minimal-positive precision was 15/21 (71.43%), below the 75% useful band, while
reference-minimal recall was 15/17 (88.24%). The 83.33% overall composition
agreement therefore does not establish useful prefer-minimal policy behavior;
#543 must replay that policy and may validly fail it.
Tie-in branded-positive recall was 2/4 (50%), below the 75% useful band, while
absent-case specificity was 44/44 (100%). The high overall tie-in agreement is
therefore driven by class prevalence and does not establish useful branded-cover
detection for an avoid-tie-in policy.

Both failed packet invocations occurred after dispatch and before result
admission. The
exact shared cause was not retained and is not proven. The 50 selected candidate
rows came from 29 distinct packets, including 25 multi-choice packets; rows
from one packet share an atomic request/result boundary, one admitted packet
result can score multiple selected rows, and those rows are correlated. The
sealed aggregate receipts record 50 candidate-scoped packet invocations and
two failures, but do not retain the opaque mapping needed to determine whether
the failures affected two distinct packets or how many distinct packets
produced at least one successful invocation. Distinct-packet dispatch coverage
is therefore unavailable and is not inferred. Successful exact preflight and
the admitted rows show that no cohort-wide always-on authentication, profile,
sandbox, binding, or schema failure occurred; bounded transient child,
resource, or packet-interaction hypotheses remain. The failures were retained
without remediation, retry, or another holdout. The 4% candidate missingness
rate is within the frozen 5% candidate-coverage tolerance, but it is not a
packet coverage rate; its bias magnitude is unknown and every quality
conclusion is conditional on the 48 admitted candidate rows.

The primary frozen gate—at least 95% candidate coverage, at least 75% overall
exact agreement for each field, and zero safety or binding violations—passes.
Under the frozen bands, admitted-row composition agreement is mixed, while
medium, promotional text, and overall tie-in agreement are strong. These
overall rates do not prove the class-specific policy uses: minimal-positive
precision and tie-in branded-positive recall are separately insufficient. #543
must report prefer-minimal and avoid-tie-in policy performance independently
and may validly fail either proof. A conservative sensitivity calculation that
treats both missing results
as disagreements gives dispatched-cohort lower bounds of 40/50 (80%) for
composition, 47/50 (94%) for medium, 45/50 (90%) for promotional text, and
46/50 (92%) for tie-in branding;
the overall per-field agreement bands are unchanged, but that calculation does
not cure the tie-in positive-recall failure. Joint exact agreement is 36/48
(75%) among admitted cases but only 36/50 (72%) under that sensitivity, so it is
missingness-sensitive and not robustly useful across all dispatched cases.
There were zero provider calls, production-library accesses, target or media
writes, or runtime activations. #501 / 0.15.1 remains unpromoted.

## Cover-policy qualification — historical deterministic contract PASS

During the initial 0.15.0 qualification attempt, before contained semantic
execution was recovered, the deterministic adjudicator passed independent code
review and replayed the unchanged random 100-packet cohort from the observation proof: 50
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
change was used to manufacture a recommendation. The newly admitted semantic
observations invalidate that all-abstention replay as current policy-quality
evidence; #543 must requalify the unchanged policy against them.

## Cover-policy development reproof — mixed, potentially useful

After the contained observer and candidate identity admission were corrected,
the policy proof froze current reference decisions before opening the retained
development outputs. The 50 retained packet observation artifacts covered all
148 development candidates, so no additional model or provider call was needed.
All eight policy combinations ran on every packet.

| Measure | Aggregate result |
| --- | ---: |
| Development packets | 50 |
| Candidate observations admitted | 148/148 |
| Policy decisions | 400 |
| Exact acceptable-set agreement | 354/400 (88.5%) |
| Exact recommendation agreement | 356/400 (89%) |
| Exact whole-decision agreement | 344/400 (86%) |
| Lowest exact policy-combination agreement | 39/50 (78%) |
| Binding, eligibility, explicit-policy, invented-reference or privacy violations | 0 |
| New model or provider calls | 0 |

The result is inside the predeclared 75–90% mixed-but-potentially-useful band.
The plain policy was exact on 50/50 packets, minimal preference alone on 45/50,
and tie-in avoidance alone on 49/50. Promotional-text avoidance was the main
remaining disagreement source at 39/50 exact across each combination that
enabled it. Because no policy fell below the useful band, changing the frozen
prompt or image packaging before confirmation would optimise a development
result rather than prove the current idea. The next formal evidence is one fresh
unused holdout under this unchanged configuration; these exposed development
results are not that holdout.

## Historical cover-policy holdout — insufficient coverage

Before selection or exposure, the fresh holdout design excluded every earlier
development, final and spent cohort across source, content, work, edition,
ISBN, packet, exact preview and near-duplicate boundaries. The retained
inventory could supply 90 eligible complete multi-choice packets. Under the
frozen complete-packet ordering, exactly ten packets could contain at most 44
candidates, so the transparent final target was ten complete packets and 44
candidates rather than subsetting packets or reacquiring covers.

All 44 visual references received independent first and second reviews. The
reviewers agreed on 164/176 fields; 12 field disagreements received an
evidence-only visual adjudication before any tested output was opened. The
accepted ledger contained zero unknown labels and produced 80 frozen policy
decisions across the eight policy combinations. A development-only Linux
canary had already admitted all three observations from one previously exposed
packet on the exact current packet-v2 transport. No provider call, corpus
rebuild, prompt change, model change, threshold reduction, production access or
write was used.

The fresh one-shot final run did not meet its frozen 95% coverage gate:

| Formal-holdout measure | Aggregate result |
| --- | ---: |
| Complete packets admitted | 2/10 (20%) |
| Candidate observations admitted | 9/44 (20.45%) |
| Policy decisions scored | 16/80 (20%) |
| Typed failures / retries | 8 `runtime` / 0 |
| Composition exact agreement | 9/9 (100%) |
| Medium exact agreement | 5/9 (55.56%) |
| Promotional-text exact agreement | 8/9 (88.89%) |
| Tie-in exact agreement | 9/9 (100%) |
| All-four exact agreement | 4/9 (44.44%) |
| Acceptable-set agreement | 12/16 (75%) |
| Recommendation agreement | 16/16 (100%) |
| Status agreement | 16/16 (100%) |
| Whole-decision agreement | 12/16 (75%) |
| Ordered-reason agreement | 16/16 (100%) |
| Strict decision-plus-reason agreement | 12/16 (75%) |
| Admitted-dispatch p50 / p95 latency | 17.01 s / 17.90 s |
| Safety, binding or invented-reference violations | 0 |

Aggregate admitted-candidate confusion was:

| Field | Reference → observer | Count |
| --- | --- | ---: |
| Composition | `dense` → `dense` | 8 |
| Composition | `minimal` → `minimal` | 1 |
| Medium | `illustrated` → `illustrated` | 4 |
| Medium | `photographic` → `illustrated` / `photographic` | 4 / 1 |
| Promotional text | `absent` → `absent` / `present` | 2 / 1 |
| Promotional text | `present` → `present` | 6 |
| Tie-in branding | `absent` → `absent` | 9 |

There were no observer unknowns in the admitted subset. All four policy
combinations without promotional avoidance were exact for both admitted
packets. Each promotional-avoidance combination was exact for one of two; the
acceptable-set difference did not change either recommendation, status or
ordered reason list. At the distinct-packet view, one of two packets matched
all eight whole and strict decisions, while both matched every recommendation,
status and ordered reason. Neither admitted packet had every candidate exact on
all four visual fields.

The eight failures occurred after dispatch began and before observation
admission. They shared the same broad runtime type and a short failure-latency
band; exact packet-v2, owner, tool, authentication, job-root, runtime and
environment preflight had passed, and two dispatches completed. This makes an
always-on cohort-wide configuration or binding defect less likely, but it does
not identify the cause. The final controller should have persisted the
contained process error's already value-free diagnostic count and digest; it
instead persisted a generic one-count runtime digest for every failure. Raw
stderr was not retained. The exact cause and missingness bias are therefore
unknown, and no retry or replacement holdout was used.

A post-run local reproduction found one bounded remediation worth testing on a
fresh cohort: a strict pre-terminal return-code-134 CLI abort can be identified
without retaining raw output. Cover orchestration now permits one fresh-process
retry after one second for that exact signal and persists only dispatch status
and the first failure's code/count/digest. This does not reinterpret the spent
holdout, broaden provider/model authority, or relax its coverage gate; any new
formal run remains independently frozen.

The admitted results demonstrate that the current model-to-observation-to-policy
path can produce correct decisions, including complete agreement for several
policy configurations. They do not establish final generalisation: coverage is
far below the frozen minimum, medium and all-four agreement are weak in the
small admitted subset, and every apparent rate has only two packet units. The
formal result is therefore **INSUFFICIENT COVERAGE**, not a negative proof that
the model cannot do the work and not a policy-quality PASS.

## Reusable cover-policy engineering benchmark — PASS

Later diagnosis reproduced the historical short runtime failures as Rust
allocation aborts under the 512 MiB child address-space limit. On a fixed
exposed-development A/B, changing only that limit to 1 GiB moved coverage from
4/8 to 8/8 packets, with no abort, retry, binding or authority change. The
benchmark therefore uses 1 GiB memory, 32 tasks, the same one-MiB file limit,
and the existing strict one-retry path only for a proven local CLI abort.

The reusable benchmark contains 10 complete packets and 36 candidates with
independent blinded labels. The reviewers agreed on 122/144 field cells; 22
disagreements were resolved from the same pixels and taxonomy before tested
outputs were opened. The accepted reference contains no unknowns and yields 80
fixed decisions across all eight policies. Reuse is intentional: this is a
versioned engineering benchmark for comparing prompt, packaging and resource
changes, not an unseen-holdout claim.

The corrected 1 GiB baseline admitted all packets and candidates without retry
or violation. It agreed on 74/80 exact whole decisions (92.5%), but the combined
tie-in-avoidance and minimal-preference policy reached 7/10, below the
predeclared 75% per-policy floor. One prompt-only experiment then clarified
that minimal composition means a simple focal layout rather than empty
background alone. Images, labels, taxonomy, model, reasoning, limits, policy,
packet order and thresholds remained fixed.

| Reusable-benchmark measure | Baseline | Clarified composition rubric |
| --- | ---: | ---: |
| Packets / candidates admitted | 10/10 / 36/36 | 10/10 / 36/36 |
| Composition exact | 25/36 (69.44%) | 26/36 (72.22%) |
| Medium exact | 29/36 (80.56%) | 33/36 (91.67%) |
| Promotional-text exact | 36/36 (100%) | 36/36 (100%) |
| Tie-in-branding exact | 34/36 (94.44%) | 35/36 (97.22%) |
| Acceptable-set exact | 76/80 (95%) | 78/80 (97.5%) |
| Recommendation exact | 78/80 (97.5%) | 78/80 (97.5%) |
| Status exact | 80/80 (100%) | 80/80 (100%) |
| Whole-decision exact | 74/80 (92.5%) | 76/80 (95%) |
| Lowest exact policy combination | 7/10 (70%) | 8/10 (80%) |
| Retry / typed failure / safety violation | 0 / 0 / 0 | 0 / 0 / 0 |

The clarified run passed the frozen coverage, per-policy and zero-violation
gates. Composition remains the least certain field and the two human reviews
also disagreed on 12/36 composition labels, so the 72.22% result is reported as
mixed rather than optimized further. The experiment proves the general
principle: admitted visual observations can support useful closed cover
preferences and recommendations. It does not prove unseen generalisation or
justify production activation by itself.

## Outcome

## Deterministic election qualification — PASS

Issue #864 freezes the public `cover-election-qualification-v1` candidate over
one development cohort and two untouched holdouts. The harness consumes private
case bindings but returns only cohort name, packet count, candidate digest and
aggregate failure counts; it cannot emit identities, values, paths, media or
per-case outcomes.

Each private case supplies an independently frozen expected ordered-reference
tuple and expected recommendation; the harness compares the implementation
directly with those values and never derives its expectation by invoking the
ranker under test.

The exact candidate digest is
`b91ae72a8c4e55c9bfe1d514c28210e22427b57a03082d2b5d1ae0b40df7c457`;
the scorer digest is
`1f87667eaa0373afddef9118976f0f091e234f19bb4a9ad64eb1d4e67afe7c3e`.

Issue #999 retains that v1 manifest and its original scorer as historical
authority and introduces `cover-election-qualification-v2`, digest
`ff705f8cb4fec150824183f4edf7f6830a7edf9d3f2ac9cd013a9f58caa976d0`.
The successor makes actual-target state explicit and measures owner readiness
independently from automatic recommendation. Original cases remain known-empty
continuity evidence; incumbent, active-fetching, timeout-recovery, and
owner-final cases are separate prospective synthetic checks and do not enlarge
or relabel the frozen real cohorts.
All cohorts passed on their first permitted exposure:

| Cohort | Packets / candidates | Confirmed / conservative exclusions | Ready | False admission / duplicate / order / recommendation / readiness errors |
| --- | ---: | ---: | ---: | ---: |
| Development | 30 / 66 | 47 / 19 | 25 | 0 / 0 / 0 / 0 / 0 |
| Holdout A | 10 / 27 | 16 / 11 | 7 | 0 / 0 / 0 / 0 / 0 |
| Holdout B | 10 / 18 | 7 / 11 | 7 | 0 / 0 / 0 / 0 / 0 |

Independent adjudication marked all 111 candidates automatic-safe. That
positive election/readiness cohort does not replace the existing public
100-case mixed identity-classifier proof, which remains the negative safety
gate with zero false plausible or verified outcomes and every conflict
preserved. No private corpus material, identity, value, path or per-case result
is recorded here. The default cover destination remains `Ask me`; the passing
manifest admits `cover_order:fixed_source_order` only as an explicit Automatic
opt-in.

Issue #546 passes as a best-effort general-principle proof of a bounded,
aggregate-safe provider-cover corpus. It establishes neither semantic quality
nor runtime activation, and it does not qualify a model or a later transport
unit. Issue #541 separately passes its bounded typed observation contract and
its aggregate-safe semantic usefulness and repeatability proof.
Issue #543 retains its deterministic contract, current-packet-v2 development
proof and historical real fail-closed evidence. Its reusable engineering
benchmark now passes at 10/10 packets, 36/36 candidates and 76/80 exact whole
decisions, with every policy at 8/10 or better and zero violations. This
establishes useful benchmark behavior while explicitly not claiming unseen
generalisation.
The tracked report contains no private corpus material, and #501 remains
unpromoted.

## 0.15.0 maintainer review package

| Question | Aggregate answer |
| --- | --- |
| Is there enough real provider material? | Yes: 597 verified previews across 315 work families, 224 multi-choice packets, and 26 packets with at least five previews. |
| Can exact covers reach the contained reviewer? | Yes: the five-preview proof passed, and the final benchmark admitted 10/10 packets and 36/36 candidates with zero retry or failure. |
| Can identity be admitted safely? | Yes: 96/99 exact on the retained qualification set, with zero false plausible/verified outcomes and every conflict preserved. |
| Can the tested model describe useful cover traits? | Yes for medium, promotional text, and tie-in branding on this benchmark; composition remains below the useful band. |
| Can those observations support policy decisions? | Yes on the reusable benchmark: 76/80 whole decisions exact and every policy at least 8/10, with zero policy or safety violation. |

The final end-to-end benchmark's contained-reviewer latency was 12.554 seconds
p50 and 14.951 seconds p95. Across acquisition, transport, observation, and
policy evaluation there was no production-library access or write, credential
exposure, provider access from the reviewer, invented reference, ineligible
selection, explicit-policy violation, hostile-evidence instruction following,
private-data leakage, or repair of invalid model output. At the 0.15.0 review
boundary the workbench remained source-only and excluded from installed
artifacts; no semantic-cover runtime CLI or HTTP operation was then activated.

The milestone therefore proves the intended general principle: a contained
multimodal model can turn exact report-bound covers into useful closed
observations, and deterministic code can turn those observations into useful
policy-compatible recommendations. It does not prove unseen generalisation,
production readiness, or reliable fine-grained composition classification.
The maintainer reviewed and accepted this package on 2026-08-12, completing
#544 and authorizing closure of tracker #500 and milestone #20. Issue #501 was
then explicitly promoted as F21 / 0.15.1 to activate owner-confirmed decisions
by reusing this retained benchmark and its limits. Promotion does not turn the
judgement labels into objective ground truth, prove unseen generalisation, or
authorize a release, automatic model-selected write, new provider acquisition,
or corpus rebuild.
