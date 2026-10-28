# ADR-0087: Bind qualification authority to permitted operations, not source bytes

- Status: accepted
- Date: 2026-10-28
- Owners: project maintainers
- Issues: #1389, #1381; deleted by #1396
- Supersedes: ADR-0073 "Compact current records", for semantic/provenance
  source ownership only; ADR-0076's semantic source closure
- Superseded by: none

## Context

ADR-0036 grants decision-only Automatic authority to the options that passed
the F19a, F20 and F46 qualifications; F38 is separate. Each current record binds
an `authority` object: family, media, semantic revision, a
`semantic_input_sha256` over the 144 paths classified semantic, and the exact
permitted operations, dispositions and pairs. The runtime constant is the
SHA-256 of that object, and every `AutomaticDecision` carries it.

Because the source digest sits inside the authority object, any byte change to
any semantic path rotates all three digests. The procedure in the
[qualification contract](../../product/rich-metadata-qualification.md#current-compact-authority)
then archives the record, bumps the semantic revision, appends the displaced
digest with its frozen permission set to the prior-manifest tables, extends the
parity test and adds a product-doc section. Measured in #1381 and since:

- 32 of 50 consecutive commits displaced qualification, adding a net 582 lines,
  about 18 per pull request, plus a separate procedure commit each.
- The prior tables hold 422 digests (F19a 141, F20 164, F46 117) over ten
  frozen permission sets, about 500 lines that exist only to decode them.
- Revisions are sequential, so parallel delivery lanes that touch semantic
  files collide and the second to merge redoes its displacement.
- Even a provenance-only edit, to a doc or a test, rewrites
  `qualification-provenance-current.json`, and adding or deleting any `src/`
  file needs classification review.
- #1382 needed two displacements for a change that altered no permitted
  operation. #1195 shows the procedure is error-prone: two refresh-only
  rotations dropped digests and nothing caught it.

The rotation does not re-qualify. Every successor section records that no
historical quality was rescored and no fresh holdout is claimed; the
measurement that justified each option is fixed in the provenance record and
never re-run. The digest proves which bytes were present, not that qualified
behaviour still holds. Behaviour is held by the unit, persistence and corpus
tests and by review, as for every other feature.

ADR-0013 applies: before 1.0.0 every persisted schema accepts only absent or
exact-current state, and a clean rebuild may be required.

## Decision

1. **Authority is the permission set.** Each current record's `authority`
   object keeps family, media, permitted operations, dispositions and pairs, and
   drops `semantic_input_sha256` and `semantic_revision`. The runtime constant
   remains the canonical SHA-256 of that object, so it rotates only when an
   operation, disposition or pair is added, removed or changed.
2. **What still rotates authority.** A change that adds, removes or alters a
   permitted operation, disposition or pair, or that changes which target write
   a permitted operation may perform. Widening, meaning a new operation or a
   broader write under an existing one, also needs fresh aggregate measurement
   evidence in the provenance record, as F20 #564 and F46 had. Narrowing
   rotates the digest without new measurement.
3. **What no longer rotates authority.** Every other source change, including
   evidence, preview, copy, persistence, scheduling and provider code. Such a
   change needs the same behaviour, security and build proof as any other
   change and nothing more.
4. **What stays frozen.** The F19a, F20 and F46 current records, the F38
   contributor-display manifest, and their measurement provenance: the
   aggregate measurements, measurement artifacts and immutable origins. They
   are the evidence for the grants and are never regenerated.
5. **What is released.** #1396 deletes it in one change that declares
   **clean rebuild required**:
   - the 563-path source classification (144 semantic, 419 provenance-only),
     its per-file source bindings, the classification pin and the AST binding
     exclusions in `scripts/verify_qualification_records.py`, which keeps only
     the record-to-runtime-constant check;
   - the per-change displacement procedure and every successor section of the
     qualification contract;
   - `_PRIOR_F19A/F20/F46_QUALIFICATION_MANIFESTS`, the ten
     `_HISTORICAL_PERMISSIONS_*` sets and the historical branch of
     `_qualification_manifest_is_valid`;
   - the #1148 retained decoding (`matches_historical_rich_outcome`), which has
     no history left to match;
   - the archive index and archived records, readable in Git history only;
   - the byte-frozen status CONTRIBUTING.md gives four F20 sources; their
     formatter and lint exemptions become ordinary lint debt;
   - earlier-version readers that serve only these tables, such as the
     `metadata-report-v26` bridge path, as far as #1396 finds them unused.
6. **What remains in `src/`.** SHA-256 literals that pin the current record
   constants, F38, cover qualification and reviewer cost evidence stay. Each
   changes only with the authority or evidence it pins.

Until #1396 merges, the verifier and parity tests still enforce the old
procedure, so changes to semantic paths keep displacing until then. After it, a
decision minted under any earlier digest is rejected as invalid; under ADR-0013
that is a clean rebuild, not a migration.

## Consequences

### Positive

- Ordinary changes stop paying the displacement commit and its lines, and
  parallel lanes stop colliding on revision numbers.
- About 500 lines of historical tables and the classification machinery leave
  `src/` and `scripts/` with no behaviour change for current decisions.
- #1195 becomes moot: no table remains for a rotation to drop entries from.

### Negative

- A source change can alter how a qualified option behaves without any digest
  change. Tests and review are the guard, as for every other feature.
- One clean rebuild when #1396 lands.

### Neutral or deferred

- Moving the qualification-state bridge and remaining qualification tooling out
  of the wheel stays #1396's scope.
- After 1.0.0, persisted decisions need a compatibility story this ADR does not
  provide.

## Alternatives considered

### Narrow the semantic partition to the qualification modules

Keep byte binding over only the qualification and decision-policy modules.
Displacement would be rarer but would still happen, the history tables would
stay and grow, and #1195 would still need its fix. It keeps the ceremony while
conceding that byte binding is not re-qualification.

### Keep the regime and script the procedure

One command could archive, bump, append and refresh. That removes the manual
commit but not the lines, the table growth, the lane collisions or the review
load, and #1396 would close with nothing to delete.

## Validation

- After #1396, a source-only change to a formerly semantic path leaves the
  three runtime constants and records unchanged, and the verifier passes.
- Changing a record's permitted operations without updating its runtime
  constant fails the verifier and the parity test.
- `AutomaticDecision.from_json` rejects a decision carrying any digest other
  than the current one.
