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

Run these from the checkout you built the install from, in one Terminal
window. They stop the container, copy `/data/application.sqlite3` and the
`/data/artifacts` folder out of it (the same moment for both), and start it
again. `docker cp` reads from the stopped container, so this works whether
your data is in Docker's named volume or in a folder you mounted. Running the
block again replaces the earlier copy.

```
OUT=~/cover-smoke
rm -rf "$OUT/smoke-copy"
mkdir -m 700 -p "$OUT/smoke-copy"
docker compose stop
C=$(docker compose ps -aq mechanical-librarian)
docker cp "$C":/data/application.sqlite3 "$OUT/smoke-copy/"
docker cp "$C":/data/artifacts "$OUT/smoke-copy/artifacts"
docker compose start
chmod -R go-rwx "$OUT"
```

The copy holds your library's records and your OpenRouter key: keep it
owner-only and delete it after the run. Keep this Terminal window open for the
next steps, or set `OUT=~/cover-smoke` again in a new one.

## 2. Preflight

```
uv run python scripts/cover_observation_smoke.py preflight \
    --database "$OUT/smoke-copy/application.sqlite3" \
    --artifacts "$OUT/smoke-copy/artifacts"
```

It picks up to 200 works with covers (spread across the library, the same ones
each time for the same copy), asks OpenRouter only for your key's status and
the model's prices, and sends no cover. It prints the model, the request count,
the worst case per request and for the whole run, the spending cap and a
confirmation code, then the command to run.

Three options change the plan. Add them to this command if you want them:
`--requests N` (at most 1,000), `--spend-cap-usd AMOUNT` ($5 by default, at
most $100) and `--model vendor/model` to smoke a model other than your saved
one.

## 3. Run

Paste the command the preflight printed, with `NEW_FOLDER` replaced by
`"$OUT/cover-smoke"`. It already carries your options and the confirmation
code.

- If the copy, an option or the model's prices changed since the preflight, the
  code no longer matches and nothing is sent. Run the preflight again.
- Requests go one at a time and are never retried.
- Before each request the run stops if that request could carry the known
  spend past the cap. The worst case uses the model's listed prices and the
  reviewer's 8,192-token answer limit, so the cap holds unless OpenRouter
  charges more than it lists.
- A request OpenRouter refuses before the model starts (statuses 400, 401,
  402, 403, 404 and 429) is counted as free. Any other failed request, or one
  whose charge is not reported, stops the run, and its charge is shown as
  unknown: check it in your OpenRouter activity.
- It also stops after five failures in a row.

The last lines say whether the smoke passed and what it spent:

| Result | Meaning |
| --- | --- |
| `passed` | Every request was sent and every answer was a valid report. |
| `failed` | At least one answer was rejected or a request failed. The line under the result counts each failure code. If any is `output_rejected`, the model cannot be relied on for the revised schema. For any other code, deal with the cause its row below names (your key, credit, privacy settings, the model or the connection) and run again. |
| `incomplete` | The run stopped early. The line under the result says why. If the cap stopped it, raise `--spend-cap-usd` or lower `--requests`. Then run again. |

To run again, move the earlier run aside so the next steps use the new one,
then run the preflight and paste its command as above:

```
mv "$OUT/cover-smoke" "$OUT/cover-smoke-$(date +%Y%m%d-%H%M%S)"
```

Failure codes:

| Code | Meaning |
| --- | --- |
| `output_rejected` | The model answered outside the revised schema: a missing, extra or unknown value, or a wrong cover. |
| `request_rejected` | The request could not be built from the copy. Copy the data again. |
| `transport` | The connection to OpenRouter failed. |
| `invalid_key`, `quota_exhausted`, `rate_limited` | OpenRouter refused the key, the account has no credit, or too many requests were sent. |
| `model_unavailable`, `model_text_only`, `data_policy_refused`, `image_limit_exceeded` | The model cannot take this request: it is gone, cannot read images, is not allowed by your privacy settings, or was sent too many images. |
| `malformed_response`, `response_too_large`, `unreachable`, `timed_out` | OpenRouter's answer was unusable, too large, or did not arrive in time. |

## 4. Label

```
open "$OUT/cover-smoke/labelling/index.html"
```

The sheet holds every cover the model marked with a tie-in, text not in
English, a placeholder or non-cover image, or prominent praise, plus as many
unmarked covers (at least 20), up to 200 in all. The model's answers are not
shown. For each cover, answer the four questions from the image alone; "Can't
tell" is an answer. Click a cover to see it full size. **Next unanswered**
takes you to the first cover still missing an answer. Answers save in the
browser as you go. Press **Save labels** when done, which downloads
`cover-labels.json`.

## 5. Score

```
uv run python scripts/cover_observation_smoke.py score \
    --run "$OUT/cover-smoke" --labels ~/Downloads/cover-labels.json \
    > "$OUT/cover-label-score.json"
```

If you saved more than once, your browser named the later files
`cover-labels (1).json` or similar. Pass the newest one, in quotes because of
the space and brackets: `--labels ~/Downloads/"cover-labels (1).json"`.

It counts, for each of the four questions, your answer against the model's,
separately for marked and unmarked covers. It sets no pass mark: whether a
dimension qualifies is decided under
[#445](https://github.com/atabbott/mechanical-librarian/issues/445).

## What to share

Upload `$OUT/cover-smoke/result.json` and `$OUT/cover-label-score.json` to
#1417. They hold counts, codes, the model name, the instruction's digest and
the spend, and nothing from your library. The rest of the run folder (each
request's report, the covers and the sheet) and `cover-labels.json` stay
private. Delete `$OUT/smoke-copy` when you are done.
