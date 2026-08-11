# Semantic cover corpus readiness — 0.15.0

Status: **PASS** (2026-08-11)

This aggregate-only readiness record reports the frozen provider-cover corpus
gate for milestone 0.15.0. It deliberately excludes corpus identities,
metadata, provider payloads, media, labels, paths, identifiers, URLs, and
per-case outcomes.

## Frozen scope and boundaries

- Inputs were real source works from the approved private corpus, through a
  disposable loopback read-only target and the existing report, provider, and
  cover-artifact seams.
- Only configured Hardcover and Open Library cover lanes were used; Google
  Books remained metadata-only.
- There was no production-library access or write, model/reviewer dispatch
  during acquisition, provider-envelope expansion, or holdout reuse.
- Source, work-family, edition/ISBN-family, and near-duplicate split checks
  passed. Final-holdout material remains unseen to implementation and tuning.

## Acquisition inventory

| Measure | Aggregate result |
| --- | ---: |
| Admitted source works | 340 |
| Completed report runs | 340 |
| Report failures | 0 |
| Distinct usable provider previews | 643 |
| Report-bound provider preview references | 790 |
| Work families with a usable preview | 272 |
| Work-family-disjoint multi-choice packets | 262 |
| Inspectable incumbents available / retained | 259 / 259 |
| Packets with at least five exact previews | 17 |
| Packets with at least six exact previews | 1 |

The 2026-08-11 packet-readiness contract requires at least ten packets with
five previews across the sealed inventory, including at least one in the
pre-frozen qualification-holdout role. It passes with seventeen and five,
respectively. This is exploratory transport readiness, not a statistical yield
or semantic-quality claim. The role assignments, acquisition behavior, and
sealed inventory were reused unchanged; no cases were moved to meet the
readiness gate.

Provider outcomes are bounded aggregate counts:

| Provider | Success | No result | Failure | Truncation |
| --- | ---: | ---: | ---: | ---: |
| Hardcover | 269 | 40 | 31 | 10 |
| Open Library | 293 | 39 | 8 | 23 |

Success, no-result, and failure are mutually exclusive terminal categories:
each provider row therefore totals 340 report runs. Truncation is an overlapping
bounded flag, not an additional terminal outcome.

Usable-preview packets have this distribution: 0: 61, 1: 10, 2: 119, 3: 76,
4: 57, 5: 16, 6: 1. The observed maximum is 6; p50 is 2 and p95 is 4.
Acquisition made zero production-library accesses or writes and zero
model/reviewer calls.

## Identity-review qualification

The 2026-08-11 evidence-coverage revision requires exactly two
work-family-disjoint 50-case cohorts. Each closed identity class must have at
least three cases in each cohort and at least eight across both cohorts. This
is a coverage gate, not a semantic-quality threshold. It retains the frozen
precision, contradiction, blinding, correction, split/leakage, and
final-holdout restrictions; no relabelling or cohort movement was used to meet
the revised minimums.

| Review measure | Result |
| --- | ---: |
| Development cohort size | 50 |
| Final-holdout cohort size | 50 |
| First reviews completed | 100 |
| Independent second reviews completed | 100 |
| First/second review agreements | 33 |
| First/second review disagreements | 67 |
| Final labels corrected from first review | 59 |
| Final resolutions differing from second review | 16 |
| Split/leakage status | passed |
| Product-prediction and score-impact blinding | passed |
| Second-review answer blinding | passed |
| Final-holdout implementation/tuning exposure | none |

| Closed identity class | Development | Final holdout | Both cohorts |
| --- | ---: | ---: | ---: |
| Verified manifestation match | 29 | 30 | 59 |
| Verified work match | 8 | 8 | 16 |
| Plausible match | 4 | 4 | 8 |
| Unknown | 6 | 3 | 9 |
| Conflict | 3 | 5 | 8 |

Every case has a sealed final adjudicated class: no final label is unresolved.
The `unknown` and `conflict` classes are closed policy outcomes that preserve
insufficient evidence or deterministic contradiction, respectively; they do
not represent unresolved ground-truth labels.

All acquisition and identity-coverage gates therefore pass. The corpus is
qualified for the bounded semantic-cover decision workbench. This does not
claim that the separate real contained-reviewer transport qualification passed:
that negative result is recorded in the contributor-workbench qualification
evidence. Downstream issue and Project-state reconciliation remains a
coordinator action. #501 / 0.15.1 remains unpromoted.
