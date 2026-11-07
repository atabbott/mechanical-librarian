# Work-route measurement

`scripts/work_route_measurement.py` measures whether Hardcover's and Open
Library's exact book matches state an audiobook's details correctly. Its result
decides whether the work route, which offers book details for an audiobook
Audible cannot match, may fill an empty field without asking (#1642). It is a
workbench tool, not part of the installed product.

Run it from the checkout that prepared the library, against a copy of the
application database:

```
uv run python scripts/work_route_measurement.py \
    --database /copy/application.sqlite3 \
    [--mark 15:0.9] [--provenance]
```

It opens the copy read-only, writes nothing and prints one
`work-route-measurement-v1` JSON document. A database that is missing, or that
this checkout cannot read, stops it. `--provenance` prints instead the short
fragment a `current_audiobook_work_route_measurement` provenance section
records.

## Before running it

Description, genres and narrators are measured from your own decisions, so
first review the work route's offers on audiobooks whose field is empty. For
each of those fields, accept the proposed value where it is right and enter or
keep your own where it is not. Only the latest decision per book and field
counts, and a withdrawn one is left out. Series and the publication date need
no review.

## Reading the output

`populations` counts current audiobooks and, for each provider, three groups:

- `recording`: Audible admitted the recording, and the provider has an exact
  match for the book. Series and dates are compared here.
- `unmatched`: no recording admitted, with an exact match. `unmatched_fill`
  counts, per field, the empty fields the work route offers that provider's
  value for: what a grant would fill.
- `duplicate`: the provider matched several different books. These are left
  out.

`contradictions` counts recording-admitted audiobooks whose exact match names
a different title or author list from the admitted recording. They are not
compared, and they count against every field of that provider.

Each field under `fields` has a `method`:

| Method | Fields | Compared against |
| --- | --- | --- |
| `recording` | `series`, `publishedDate` | Your current series, or else the series the recording match chose (unless only this provider chose it); your current year, or else the other provider's year. Positions agree when equal or when either is missing (`position_absent`). |
| `owner_labelled` | `description`, `genres`, `narrators` | Your decisions: accepting this provider's value agrees; keeping the field empty, entering a value, rejecting or choosing another source's value disagrees. `labels` counts the decisions used and set aside. |
| `not_measured` | `cover` | Nothing yet: covers have no identity check (`no_identity_basis`). |

For each provider and pooled across both, a field reports `compared`, `agree`,
`disagree`, `not_comparable` with its reasons, `contradictions`, `agreement`
and a 95% Wilson interval, which is for information only. `publishedDate` also
reports `cross_provider`: how often Hardcover's and Open Library's years agree
on the same audiobook.

## The pass mark

Each entry in `marks` passes when at least `min_compared` were compared,
`agreement` is at least `min_agreement` and there are no more than
`max_contradictions`. The owner's mark is the first default: 20 compared, 95%
agreement and no contradictions. The second default, 10 at 90%, and any
`--mark N:P[:C]` you add show what a looser mark would grant if the library is
too small for the first.

## Privacy

The output holds provider names, field names, closed reason codes and counts.
It never holds a title, author, value, path, configured name or identifier.
