# Bounded reviewer collection (#1060)

The owner-authorized collection retained **324 model request attempts**:
322 strict successes, one pre-response failure with an unknown charge and one
charged invalid-JSON failure. Known paid spend was **$0.1494260638**; the
unresolved charge is additional, not zero. No automatic policy is qualified.

## Cohort and method

The closed report-only cohort contains 100 reports: 50 ebook and 50 audiobook,
representing 50 matched work keys. There were 91 usable cover packets
(47 ebook, 44 audiobook), 307 candidate occurrences and 266 distinct transport
images. Nine reports lacked a packet (three ebook, six audio). The producer's
candidate cap of 12 and 244 omissions were retained without collection
truncation; the observed maximum was nine candidates per packet.
Description eligibility was 84 reports (38 ebook, 46 audio); the first 50
eligible reports in original order were selected (25 per medium), representing
37 matched work keys. Sixteen were ineligible; 34 eligible reports were
unselected. This is a convenience cohort with correlated pairs and variants.

All calls used installed, qualified source
`4f9264a1e8759d55a40bb58129ab0820d9bfe142`. OpenRouter covers used
`openai/gpt-6-luna` and `xiaomi/mimo-v2.6-flash`; descriptions used Luna.
Contained covers used `gpt-5.6-sol`, medium reasoning, with the unchanged
bounded profile and sandbox. Only supported cover previews and sealed packets,
or projected descriptions and cleanup preferences, were task inputs. Reviewers
received no library credentials, media files or write authority. Raw inputs,
final outputs and immutable captures remain private. Network-disabled local
replay reproduced every successful strict result and its retained bindings.

## Completion, cost and failures

| Phase | Outcome | Known paid cost (USD) |
| --- | --- | ---: |
| Initial mixed selection | Incomplete: 91 Luna covers, nine MiMo successes, one unknown-charge failure | 0.074629810 |
| Approved 82-packet MiMo continuation | Incomplete: ten successes, one charged JSON failure; 71 undispatched | 0.0088283608 |
| Remaining 71 MiMo packets | Complete: 71 strict successes | 0.057402618 |
| First 50 eligible descriptions | Complete: 50 strict successes | 0.008565275 |
| 91 contained cover packets | Complete: 91 strict successes | Plan allowance |

MiMo's 92 attempts cover 91 packets: an explicitly approved retry followed
the unknown-charge failure. Its later charged response contained extra data
after JSON; strict parsing admitted no candidate observations. That failure
was not retried. No candidate-binding failure was observed. All descriptions
returned ready advice, with no abstentions. Contained covers used exactly
91 processes, one per packet, with no local-abort retry. Subscription tokens
and a monetary rate are not exposed by the capture and are not estimated.

The first two phases stopped at the first failure. The owner then authorized
continuing past known-charge MiMo parse failures; unknown charges still stopped
dispatch. Descriptions and contained covers retained stop-on-failure. Successful
requests were not repeated by continuations. The cumulative target was $2,
with a $1.50 known-spend stop before the next paid dispatch; an in-flight call
could exceed either. Preliminary smoke runs are separate: six paid successes
($0.002389755), two contained successes and one local pre-dispatch setup failure.
They are excluded from all collection totals and distributions here.

The [packaged evidence](../reference/reviewer-cost-evidence-v2.json) carries
the native 50-description row for Settings: $0.1713055000 per 1,000 attempted
description requests. Its Luna cover row comes from the
[later cover run](#later-luna-cover-run-2026-10-13); other model/task estimates
remain unavailable in the application.
The [native 71-MiMo continuation evidence](../reference/reviewer-cost-cover-continuation-v2.json)
is documentation-only: $0.8084875774647887323943661972 per 1,000 cover requests
on that continuation subset, not a whole-cohort comparison. Each retains its
own cohort digest and observation time. The native `works` field counts input
report indices (50 and 71), not distinct literary works. Cost denominators are
attempted requests, never images or successful outputs alone.

Each evidence row carries its own measured run. Different phases are not
combined under invented provenance. The original mixed run remains incomplete,
even though all 91 Luna covers succeeded with known cost $0.066307675. All MiMo
attempts together have known cost $0.0745531138 across 91 known-charge outcomes
plus one unresolved charge; this is not a complete estimate. Charged failures
remain in known-charge accounting. No unknown charge is replaced with zero.

## Independent declared-sample inspection

Before viewing outputs, inventory selected first, quarter, middle,
three-quarter, last and an additional largest packet by medium. The fixed
sample contains 12 reports, 11 matched work keys, 43 image occurrences and
42 distinct transport digests (also 42 distinct preview digests). Inspection
covered 125 correlated candidate-model observations: 43 Luna, 39 MiMo and
43 contained. Four MiMo observations remain unavailable after the JSON failure;
no successful substitutes were selected. Six description projections, three
per medium, were compared with their own outputs.

Confirmed sample findings were four Luna photographic-to-mixed errors, one
Luna observation returning unknown for all twelve dimensions despite visible
evidence, two contained promotional-text false positives on author references,
and two contained degradation false positives on clean 320-pixel images.
Narrator-credit format interpretation was probable but not definitive;
roundels, composition boundaries and some imagery/medium labels were ambiguous.
All six description samples were faithful to their supplied projection, with
no unsupported factual additions or retained promotional language found. This
does not verify an external edition or all 50 descriptions.

These are sample-only findings, not accuracy rates or model rankings. Agreement
between models is not ground truth. Objective placeholder, title-legibility,
reproduction and visible-format observations could inform later independent
defect qualification, but current false positives bar activation. Medium,
composition and imagery remain owner preferences. Ambiguous promotional,
roundel and format vocabulary needs independent labels. No policy, ranking,
application permission or automatic behavior changes follow from this study.

## Completion by model and medium

| Model | Medium | Task | Attempts | Strict successes | Failures |
| --- | --- | --- | ---: | ---: | ---: |
| openai/gpt-6-luna | ebook | cover | 47 | 47 | 0 |
| openai/gpt-6-luna | audiobook | cover | 44 | 44 | 0 |
| xiaomi/mimo-v2.6-flash | ebook | cover | 48 | 46 | 2 |
| xiaomi/mimo-v2.6-flash | audiobook | cover | 44 | 44 | 0 |
| openai/gpt-6-luna | ebook | description | 25 | 25 | 0 |
| openai/gpt-6-luna | audiobook | description | 25 | 25 | 0 |
| gpt-5.6-sol | ebook | cover | 47 | 47 | 0 |
| gpt-5.6-sol | audiobook | cover | 44 | 44 | 0 |

## Dimension distributions

All admitted cover-language observations were English or unknown, and no
placeholder-positive case was observed. Non-English cover language and positive
placeholder strata therefore remain unqualified.

Counts are candidate occurrences in strictly parsed outputs, not independent
images or correctness scores. Missing outputs are excluded; `unknown` is an
admitted abstention. Correlated variants and paired works remain in the counts.

### openai/gpt-6-luna — ebook

| Dimension | Observed category counts |
| --- | --- |
| composition | dense: 113, minimal: 96, unknown: 4 |
| cover_text_language | english: 212, unknown: 1 |
| explicit_imagery | absent: 202, suggestive: 10, unknown: 1 |
| format_branding | absent: 197, audiobook: 15, unknown: 1 |
| medium | illustrated: 135, mixed: 11, photographic: 50, typographic: 14, unknown: 3 |
| overlay_marking | absent: 207, present: 2, unknown: 4 |
| placeholder | absent: 210, unknown: 3 |
| promotional_text | absent: 52, present: 157, unknown: 4 |
| reproduction | clean: 199, degraded: 10, unknown: 4 |
| series_styling | omnibus_or_set: 2, series_branded: 68, standalone: 140, unknown: 3 |
| thumbnail_legibility | illegible: 1, legible: 209, unknown: 3 |
| tie_in_branding | absent: 200, present: 10, unknown: 3 |

### openai/gpt-6-luna — audiobook

| Dimension | Observed category counts |
| --- | --- |
| composition | dense: 39, minimal: 55 |
| cover_text_language | english: 94 |
| explicit_imagery | absent: 88, suggestive: 6 |
| format_branding | absent: 70, audiobook: 23, text_edition: 1 |
| medium | illustrated: 74, mixed: 4, photographic: 13, typographic: 3 |
| overlay_marking | absent: 93, present: 1 |
| placeholder | absent: 94 |
| promotional_text | absent: 41, present: 53 |
| reproduction | clean: 90, degraded: 4 |
| series_styling | series_branded: 41, standalone: 47, unknown: 6 |
| thumbnail_legibility | legible: 94 |
| tie_in_branding | absent: 88, present: 6 |

### xiaomi/mimo-v2.6-flash — ebook

| Dimension | Observed category counts |
| --- | --- |
| composition | dense: 39, minimal: 170 |
| cover_text_language | english: 209 |
| explicit_imagery | absent: 207, suggestive: 2 |
| format_branding | absent: 195, audiobook: 14 |
| medium | illustrated: 131, mixed: 7, photographic: 58, typographic: 13 |
| overlay_marking | absent: 198, present: 10, unknown: 1 |
| placeholder | absent: 209 |
| promotional_text | absent: 50, present: 158, unknown: 1 |
| reproduction | clean: 196, degraded: 10, unknown: 3 |
| series_styling | omnibus_or_set: 5, series_branded: 66, standalone: 127, unknown: 11 |
| thumbnail_legibility | illegible: 2, legible: 207 |
| tie_in_branding | absent: 198, present: 11 |

### xiaomi/mimo-v2.6-flash — audiobook

| Dimension | Observed category counts |
| --- | --- |
| composition | dense: 15, minimal: 79 |
| cover_text_language | english: 94 |
| explicit_imagery | absent: 94 |
| format_branding | absent: 65, audiobook: 28, text_edition: 1 |
| medium | illustrated: 63, mixed: 2, photographic: 21, typographic: 8 |
| overlay_marking | absent: 92, present: 2 |
| placeholder | absent: 94 |
| promotional_text | absent: 43, present: 51 |
| reproduction | clean: 92, degraded: 2 |
| series_styling | series_branded: 44, standalone: 43, unknown: 7 |
| thumbnail_legibility | legible: 94 |
| tie_in_branding | absent: 88, present: 6 |

### gpt-5.6-sol — ebook

| Dimension | Observed category counts |
| --- | --- |
| composition | dense: 87, minimal: 126 |
| cover_text_language | english: 213 |
| explicit_imagery | absent: 197, suggestive: 16 |
| format_branding | absent: 200, audiobook: 13 |
| medium | illustrated: 142, photographic: 50, typographic: 21 |
| overlay_marking | absent: 205, present: 8 |
| placeholder | absent: 213 |
| promotional_text | absent: 47, present: 166 |
| reproduction | clean: 183, degraded: 30 |
| series_styling | omnibus_or_set: 7, series_branded: 64, standalone: 142 |
| thumbnail_legibility | illegible: 1, legible: 212 |
| tie_in_branding | absent: 201, present: 12 |

### gpt-5.6-sol — audiobook

| Dimension | Observed category counts |
| --- | --- |
| composition | dense: 33, minimal: 61 |
| cover_text_language | english: 94 |
| explicit_imagery | absent: 80, suggestive: 14 |
| format_branding | absent: 63, audiobook: 30, text_edition: 1 |
| medium | illustrated: 71, mixed: 2, photographic: 14, typographic: 7 |
| overlay_marking | absent: 93, present: 1 |
| placeholder | absent: 94 |
| promotional_text | absent: 42, present: 52 |
| reproduction | clean: 90, degraded: 4 |
| series_styling | series_branded: 43, standalone: 51 |
| thumbnail_legibility | legible: 94 |
| tie_in_branding | absent: 88, present: 6 |

## Later Luna cover run (2026-10-13)

A separate complete run measured `openai/gpt-6-luna` covers: 100 requests over
100 reports, 50 audiobook and 50 ebook cover-eligible current reports drawn
deterministically from a retained closed full-library snapshot. It sent 270
distinct images. Every request completed with a known charge and none failed:
$0.0636415 in total, $0.636415 per 1,000 attempted cover requests. The
measurement tool at that source emitted the earlier single-run evidence shape;
the row is restated unchanged, with its own run provenance, in the packaged
evidence.
