# Cover observation smoke

`scripts/cover_observation_smoke.py` runs the owner's live smoke of the
revised cover observations ([ADR-0098](../architecture/decisions/0098-revised-cover-observation-vocabulary.md),
[#1417](https://github.com/atabbott/mechanical-librarian/issues/1417)). It sends
your library's covers to your cover reviewer model with the revised instruction
and schema, checks that every answer is a valid report, and then gives you a
sheet to label the rare faults the revised vocabulary looks for. It is a
workbench tool, not part of the installed product. The revised vocabulary is
not used anywhere in the app until a later change activates it, and only after
this smoke and your labels.

Only OpenRouter models are supported. The run uses your saved cover reviewer
model unless you pass `--model`, and the OpenRouter key saved in the app.

## 1. Copy the data

Stop the container, copy `/data/application.sqlite3` and the `/data/artifacts`
folder (they must come from the same moment), and start it again. Run the
script from the checkout you built the install from; a different checkout may
not read the copy.

With Docker's default named volume, from your checkout with `$OUT` set to a
private folder outside it:

```
docker compose stop
VOL=$(docker volume ls -q | grep 'mechanical-librarian-data$')
mkdir -p "$OUT/smoke-copy"
docker run --rm -v "$VOL":/data:ro -v "$OUT/smoke-copy":/out debian:bookworm-slim sh -c \
  "cp /data/application.sqlite3 /out/ && cp -R /data/artifacts /out/artifacts &&
   chown -R $(id -u):$(id -g) /out && chmod -R go-rwx /out"
docker compose start
```

With a mounted folder, copy the same two items from it with `cp -R` while the
container is stopped. The copy holds your library's records and your
OpenRouter key: keep it owner-only and delete it after the run.

## 2. Preflight

```
uv run python scripts/cover_observation_smoke.py preflight \
    --database "$OUT/smoke-copy/application.sqlite3" \
    --artifacts "$OUT/smoke-copy/artifacts" \
    [--requests 200] [--spend-cap-usd 5] [--model vendor/model]
```

It picks up to `--requests` works with covers (200 by default, spread across
the library, the same ones each time for the same copy), asks OpenRouter only
for your key's status and the model's prices, and sends no cover. It prints the
model, the request count, the most one request can cost, the worst case for the
whole run, the spending cap and a confirmation code, then the command to run.

## 3. Run

Repeat the same arguments with `run`, a new folder and the code:

```
uv run python scripts/cover_observation_smoke.py run \
    --database "$OUT/smoke-copy/application.sqlite3" \
    --artifacts "$OUT/smoke-copy/artifacts" \
    --output "$OUT/cover-smoke" --confirm CODE
```

- If the copy, an option or the model's prices changed since the preflight, the
  code no longer matches and nothing is sent. Run the preflight again.
- Requests go one at a time and are never retried.
- Before each request the run stops if that request could carry the known
  spend past the cap. Its most possible cost uses the model's listed prices and
  the reviewer's 8,192-token answer limit, so the cap holds unless OpenRouter
  charges more than it lists.
- It also stops when a charge is not reported and after five failures in a row.

The last lines say whether the smoke passed and what it spent:

| Result | Meaning |
| --- | --- |
| `passed` | Every request was sent and every answer was a valid report. |
| `failed` | At least one answer was rejected or a request failed. The model cannot be relied on for the revised schema. |
| `incomplete` | The run stopped early (cap, unreported charge, five failures in a row, or Ctrl-C). Start again with a new folder. |

Failure codes: `output_rejected` means the model answered outside the revised
schema (a missing, extra or unknown value, or a wrong cover); the others are
OpenRouter's (`rate_limited`, `timed_out`, `quota_exhausted` and so on).

## 4. Label

Open `$OUT/cover-smoke/labelling/index.html` in your browser. It holds every
cover the model marked with a tie-in, text not in English, a placeholder or
non-cover image, or prominent praise, plus as many unmarked covers (at least
20), up to 200 in all. The model's answers are not shown. For each cover,
answer the four questions from the image alone; "Can't tell" is an answer.
Answers save in the browser as you go. Press **Save labels** when done, which
downloads `cover-labels.json`.

## 5. Score

```
uv run python scripts/cover_observation_smoke.py score \
    --run "$OUT/cover-smoke" --labels ~/Downloads/cover-labels.json \
    > "$OUT/cover-label-score.json"
```

It counts, for each of the four questions, your answer against the model's,
separately for marked and unmarked covers. It sets no pass mark: whether a
dimension qualifies is decided under
[#445](https://github.com/atabbott/mechanical-librarian/issues/445).

## What to share

Upload `$OUT/cover-smoke/result.json` and `$OUT/cover-label-score.json` to
#1417. They hold counts, codes, the model name, the instruction's digest and
the spend, and nothing from your library. The rest of the run folder (each
request's report, the covers and the sheet) and `cover-labels.json` stay
private; delete the copy from step 1 when you are done.
