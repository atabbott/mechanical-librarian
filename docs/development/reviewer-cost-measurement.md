# Measuring reviewer request costs

`scripts/measure_reviewer_cost.py` measures what reviewer requests cost over a
fixed cohort of retained reports. It is an owner-run, report-only tool.
Software delivery never authorises paid calls or use of a live library, and
ordinary application reads never run it. Settings shows the published results
([configuration](../reference/configuration.md)).

## Inputs

- An owner-private copy of an exact-current, closed report database and its
  retained cover artifact directory. Do not use an active database: the
  read-only reader rejects sidecars and changed authority. No media or target
  service is needed.
- A private `0600` cohort file listing ordered, unique report IDs:

  ```json
  {"schema_version":"reviewer-cost-cohort-v1","report_ids":["report identity from the retained store"]}
  ```

- The environment, wheel and JSON receipt from a successful exact-artifact
  qualification of the source checkout (the qualifier's `--wheel-output` and
  `--environment` options). The tool builds, installs and repairs nothing. Run
  it with that environment's Python, outside the checkout. The wheel,
  installed payload, RECORD, origin and receipt must match the full source
  SHA; editable installs are refused.

## Inventory (default, no network)

```sh
"$INSTALLED_PYTHON" "$SOURCE/scripts/measure_reviewer_cost.py" \
  --database "$REPORT_DATABASE" --artifacts "$COVER_ARTIFACTS" \
  --cohort "$COHORT" --cover-model openai/gpt-6-luna \
  --description-model openai/gpt-6-luna > "$PRIVATE_INVENTORY"
```

The inventory binds each report, packet, image and description projection. It
makes no network request and starts no contained reviewer.

- One cover request is one report's packet, which may hold several images. The
  producer's candidate limit and omissions are kept; images are not truncated.
- Description models default to none. A selected description model takes the
  first 50 eligible reports in cohort order, or `--description-count`. A
  shortage stays a shortage; inputs are never duplicated.
- Cover and description eligibility are independent.

## Preflight (optional)

`--preflight --key-file "$PRIVATE_OPENROUTER_KEY"` makes only the bounded
capability and pricing GETs for the named models. It sends no chat request and
grants no spending permission. Keep the bare key in an owner-only file, never
on the command line.

A description model needs valid prompt and completion prices. A missing
catalogue request fee stays unknown and does not block measurement; an
invalid one refuses the model. Measured spend comes only from actual response
charges.

## Paid run

Only after the owner approves the cohort, model and task choices, data use and
a monitored spending envelope, repeat the inventory arguments with:

```sh
--execute --source-head "$FULL_SOURCE_SHA" \
--wheel "$RETAINED_WHEEL" --receipt "$SUCCESSFUL_RECEIPT" \
--installed-python "$INSTALLED_PYTHON" \
--confirmed-cohort "$COHORT_SHA256" --confirmed-inventory "$INVENTORY_SHA256" \
--confirm-data-use --key-file "$PRIVATE_OPENROUTER_KEY" \
--budget-target-usd 1 --stop-threshold-usd 0.80 --output "$NEW_PRIVATE_RUN"
```

The amounts are placeholders. Target and threshold are positive decimals, with
threshold at most target.

- Dispatch is sequential. It stops before the next request once known spend
  reaches the threshold. **This is not a hard cap**: the last in-flight request
  can exceed threshold and target. No account limit is changed.
- The tool never retries an OpenRouter request.
- An OpenRouter attempt with an unknown or ambiguous charge stops dispatch at
  once, with or without `--stop-on-failure`, and makes the run incomplete. A
  timeout, interruption or undispatched input also makes it incomplete. An
  unknown charge is never replaced with zero or an estimate.
- `--stop-on-failure` stops before the next dispatch after any failure, after
  retaining the attempt and any known charge. The run stays incomplete with no
  public rows, even when the failure is on the last attempt; the manifest
  records the option. Without it, other failures (a charged parse failure or a
  contained failure) are recorded and the run continues.
- For contained covers, pass `--codex-model` and `--contained-profile` instead
  of an OpenRouter cover model. These use the signed-in plan allowance and
  produce no monetary row. The supported profile keeps the reviewer's single
  local-abort retry ([contributor workbench](contributor-workbench.md#cover-packet-scenarios)),
  recorded in private `attempt_evidence`. A contained run grants no process,
  credential, tool or model authority beyond the selected profile.

## Output

The output directory is created exclusively with private permissions. Its
immutable manifest binds provenance, frozen inputs, selections, data use and
budget. Each attempt is recorded durably before dispatch and keeps parsed
output, supported final response bytes, timing, generation ID, usage, charge
and prompt, schema and profile identities. Credentials, hidden reasoning and
unrestricted streams are excluded. A started attempt without a terminal record
is interrupted and its cost unknown. There is no resume: an approved retry is
a new directory.

Only a complete run emits public aggregate rows: every input dispatched,
every charge known, and not stopped by `--stop-on-failure`. Completed means
a strict parse success. Charged parse failures are failures but stay in the
denominator: per-1,000 cost is total known charges divided by attempted
requests, times 1,000. Images and successful outputs are never the
denominator.

## Publishing results

Publishing is a separate reviewed change, never a side effect of the tool.
Never copy private capture files into the repository.

- [`reviewer-cost-evidence-v2.json`](../reference/reviewer-cost-evidence-v2.json)
  is the evidence Settings shows. It must be byte-identical to
  `src/mechanical_librarian/resources/reviewer-cost-evidence-v2.json`. Each row
  is one model and task from one complete run, with that run's source head,
  cohort digest and observation time. Rows from different runs are never
  merged. The `works` field counts input reports, not distinct works.
- [`reviewer-cost-cover-continuation-v2.json`](../reference/reviewer-cost-cover-continuation-v2.json)
  is a documentation-only row for a partial cover cohort. The application does
  not load it.

`tests/unit/test_reviewer_cost_evidence.py` checks both files.
