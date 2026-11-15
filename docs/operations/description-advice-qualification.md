# Description advice qualification

With **Automatically apply ready description advice** on, the app writes the
adviser's text without asking and may replace a description you have.
[ADR-0099](../architecture/decisions/0099-qualify-automatic-description-advice.md)
uses it only once your decisions show the adviser is right often enough. This
tool lists the descriptions to decide and scores your decisions. It sends
nothing to a model and writes nothing. Run it from the checkout you built the
install from.

## Choose the adviser

1. Right after setup, choose the description adviser in Settings → Reviewer.
   For a measured model, Settings shows "Example cost per 1,000 works". The
   app asks the adviser once for each work that has a source description.
2. In Policy → Curation → Description advice, set the cleanup choices you
   would use and leave the automatic switch off. Labels count only for the
   cleanup choices they were made under.
3. Wait until the app shows "Preparation idle".

## List the descriptions

Copy the database as [owner labels](owner-labels.md#copy-the-database)
describes, then:

```
uv run python scripts/description_advice_labels.py sample --database "$COPY"
```

It prints the adviser, then one count per line:

- To decide counts the descriptions where the adviser's text could be written
  automatically and you can choose it, replacing a description or filling an
  empty one. A link follows for each, up to the labels the bar still needs.
- Already decided counts works whose decision is already a label.
- Advice you cannot choose counts works where every source repeats your
  description, so the page offers no choice
  ([ADR-0099](../architecture/decisions/0099-qualify-automatic-description-advice.md),
  Decision 6).
- Advice marked as needing you counts text the adviser did not mark ready.
- Below the description floor counts text with markup, a visible cut-off, a
  placeholder or mostly quoted praise. The description floor is the check
  every automatic description write must pass.
- The same as the current description counts text that would change nothing.
- Out of date counts advice made before the work, the adviser or your cleanup
  choices changed.
- Waiting, request failed and no source description count works with no
  advice to judge.
- The last two lines give your labels for this adviser and the labels the bar
  still needs.

A copy whose settings are not saved in the app, made by another version, with
no adviser chosen, or with the automatic switch on is refused with the reason.

## Decide each description

Open the links in order, without skipping any. On each work's page:

- Choose **Description adviser** if you are content for that text to be
  written without being asked.
- Otherwise choose **Current value**, or a source's description you prefer.
- Do not press **Retry description advice**. Choosing or keeping after a
  retry counts as wrong for the advice it replaced.

Your latest choice on a work counts. Leave a link whose advice is out of date.
Copy the database again and rerun `sample` until it says your labels meet the
bar or nothing is left to decide.

## Score your decisions

```
uv run python scripts/description_advice_labels.py score --database "$COPY"
uv run python scripts/description_advice_labels.py score --database "$COPY" \
    --format json > ~/ml-labels/description-advice-score.json
```

For each adviser identity, the score shows your right and wrong labels, the
Wilson 95% lower limit, and whether it meets your bar of 0.90: 35 labels with
no error, 53 with one, 69 with two. The JSON holds counts, digests, engine and
model only. Attach `description-advice-score.json` to #1886. Then delete the
folder, which also holds the copy:

```
rm -r ~/ml-labels
```

`sample` also takes `--origin https://host:port` if you open Mechanical
Librarian at another address than http://localhost:8080, and `--limit N` (1 to
500) for that many links.
