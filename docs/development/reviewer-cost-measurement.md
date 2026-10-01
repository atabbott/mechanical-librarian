# Measuring reviewer request costs

This owner-run, report-only tool supports the separately authorized collection
in #1060. Software delivery does not authorize paid calls or use of a live
library. Ordinary application reads never run it. Shipped public evidence is
empty until an entire independently inspected measurement is accepted.

Use an owner-private copy of an exact-current, closed report database and its
retained cover artifact directory. Do not use an active database: the read-only
reader rejects sidecars and changed authority. No media or target-service access
is needed. Create a private `0600` cohort file with ordered, unique report IDs:

```json
{"schema_version":"reviewer-cost-cohort-v1","report_ids":["report identity from the retained store"]}
```

Use the source checkout matching a successful exact-artifact qualification.
Retain its wheel and environment with the qualifier's `--wheel-output` and
`--environment` options, and retain its successful JSON receipt. The measurement
tool does not build, install, qualify, repair, or upgrade anything. Invoke the
script with that retained environment's Python, outside the source checkout.
The wheel, installed payload, RECORD, origin and receipt must match the supplied
full source SHA. Editable installations are refused.

First produce a local inventory; this default mode makes no network request and
starts no contained reviewer process:

```sh
"$INSTALLED_PYTHON" "$SOURCE/scripts/measure_reviewer_cost.py" \
  --database "$REPORT_DATABASE" --artifacts "$COVER_ARTIFACTS" \
  --cohort "$COHORT" --cover-model openai/gpt-6-luna \
  --description-model openai/gpt-6-luna > "$PRIVATE_INVENTORY"
```

Description models default to none. Explicit description selections take the
first 50 eligible reports in cohort order, or the positive
`--description-count`; shortages remain shortages and never duplicate inputs.
Cover and description eligibility are independent. One cover request is one
work packet, potentially containing several images. The installed producer's
candidate limit and omissions are retained without truncating its image tuple.
The inventory binds each report, packet, image and description projection.

Optional `--preflight --key-file "$PRIVATE_OPENROUTER_KEY"` performs only the
existing bounded capability/pricing GETs for the named models. It makes no chat
request and starts no contained process. Description measurement requires valid
prompt and completion token prices; an absent catalogue request fee remains
unknown and does not block measurement. A present invalid request fee still
refuses the model. Actual response charges alone determine measured spend; an
unknown catalogue fee is never substituted with zero. Settings still requires
a known request fee when testing, selecting or refreshing a description model.
Keep the bare key in an owner-only regular file, never on the command line. Preflight is an explicit external read;
it is not a spending permission or an account-limit operation.

Only after the owner approves the actual cohort, model/task choices, existing
data-use posture and monitored spending envelope, repeat the same inventory
arguments with:

```sh
--execute --source-head "$FULL_SOURCE_SHA" \
--wheel "$RETAINED_WHEEL" --receipt "$SUCCESSFUL_RECEIPT" \
--installed-python "$INSTALLED_PYTHON" \
--confirmed-cohort "$COHORT_SHA256" --confirmed-inventory "$INVENTORY_SHA256" \
--confirm-data-use --key-file "$PRIVATE_OPENROUTER_KEY" \
--budget-target-usd 1 --stop-threshold-usd 0.80 --output "$NEW_PRIVATE_RUN"
```

The example amounts are placeholders for an approved envelope, not recommended
spend. Target and threshold must be positive finite decimals, with threshold at
most target. Dispatch is sequential and stops before the next request once
known spend reaches the threshold. The last in-flight request can exceed both
threshold and target. **This is not a hard cap.** No account limit is changed.
Missing or ambiguous charge, timeout, interruption or undispatched input makes
the run incomplete. No price arithmetic, history lookup or assumed zero replaces
an unknown charge. The tool and OpenRouter path never retry.

For a bounded smoke that must stop on any failure, also pass
`--stop-on-failure`. After retaining the failed attempt and any known charge,
it stops before the next dispatch, including after a contained failure or a
charged parse failure. The run remains incomplete even if the failed attempt
was last, and emits no public aggregate. Its private manifest records the
enabled option. Without the flag, known-charge parser failures retain the
existing collection behavior and remain in the attempted-request denominator.

For a contained cover run, select the exact `--codex-model` and
`--contained-profile` instead of an OpenRouter cover model. Its supported tuple
retains one existing bounded local-abort retry; private `attempt_evidence`
discloses it. It consumes the signed-in plan allowance and emits no monetary row.
This does not widen contained process, credential, tool or model authority.

The output directory is created exclusively with private permissions. Its
immutable manifest binds provenance, frozen inputs, selections, data use and
budget. Every attempt starts durably before dispatch and retains terminal parsed
output, supported final response bytes when available, timing, generation ID,
usage, known/unknown charge and prompt/schema/profile identities. Credentials,
hidden reasoning and unrestricted streams are excluded. A start without a
terminal record is interrupted and financially unknown. There is no resume;
an explicitly approved retry is a new directory preserving the original.

Only a completely dispatched run with every paid charge known may contain
public aggregate rows. Completed means strict parse success; charged parser
failures remain separate failures and remain in the attempted-request
cost denominator. Per-1,000 cost is total known charges divided by attempted
requests times 1,000, never images or successful outputs. Threshold stops and
unknown or interrupted attempts retain private incomplete evidence only.
Do not copy private capture files into the repository. The closed public model
admits only run provenance and aggregate model/task rows; public documentation
and the packaged JSON must be byte-identical. Publishing measurements is a
separate reviewed action, not a tool side effect.
