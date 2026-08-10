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
| Admitted source works | 341 |
| Completed report runs | 340 |
| Report failures | 1 |
| Distinct usable provider previews | 643 |
| Report-bound provider preview references | 790 |
| Work families with a usable preview | 274 |
| Multi-choice packets | 269 |
| Work-family-disjoint multi-choice packets | 264 |
| Inspectable incumbents available / retained | 0 / 0 |
| Packets with at least five exact previews | 16 |
| Packets with at least six exact previews | 1 |

The 2026-08-11 revision from six to five exact previews passes the revised
packet-scale acquisition gate: at least ten packets are available, with sixteen
observed. The prior six-preview minimum does not pass.

Provider outcomes are bounded aggregate counts:

| Provider | Success | No result | Failure | Truncation |
| --- | ---: | ---: | ---: | ---: |
| Hardcover | 269 | 0 | 71 | 10 |
| Open Library | 293 | 0 | 47 | 23 |

Success, no-result, and failure are mutually exclusive terminal categories:
each provider row therefore totals 340 report runs. Truncation is an overlapping
bounded flag, not an additional terminal outcome.

Usable-preview packets have this distribution: 0: 61, 1: 10, 2: 119, 3: 75,
4: 59, 5: 15, 6: 1. The observed maximum is 6; p50 is 2 and p95 is 4.

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
| First-review corrections accepted | 38 |
| Second-review corrections accepted | 47 |
| Unresolved disagreements | 47 |
| Split/leakage status | passed |
| Product-prediction and score-impact blinding | passed |
| Second-review answer blinding | passed |
| Final-holdout implementation/tuning exposure | none |

| Closed identity class | Development | Final holdout | Both cohorts |
| --- | ---: | ---: | ---: |
| Verified manifestation match | 15 | 11 | 26 |
| Verified work match | 5 | 3 | 8 |
| Plausible match | 3 | 5 | 8 |
| Unknown | 8 | 15 | 23 |
| Conflict | 19 | 16 | 35 |

All acquisition and identity-coverage gates therefore pass. The corpus is
qualified for the bounded semantic-cover decision workbench; downstream issue
and Project-state reconciliation remains a coordinator action. #501 / 0.15.1
remains unpromoted.
