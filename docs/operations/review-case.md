# Review-case admission

Admit one exact current ProposalSet:

```sh
mechanical-librarian review-case-admit --config config.json --database reports.sqlite \
  --artifact-store artifacts --proposal-set-id proposal_set_<64-lowercase-hex-characters>
```

The typed canonical result is `admitted`, `replayed`, `superseded`, `no_case`,
`retired`, or `stale`. Only `propose_change` and `unresolved_choice` results
create a case; `no_case` and `retired` have no empty case. Untrusted failures
are redacted as fixed `invalid`, `incompatible`, `currentness`, or
`persistence` codes.
`superseded` and `retired` require an active prior case. After a `no_case` or
`retired` checkpoint, the next actionable set is `admitted` and another
nonactionable set is `no_case`.
An unavailable external currentness preflight is `currentness`; repository
read or write failure remains `persistence`.

The complete case binds the exact ProposalSet, report, target and file identity.
Inside the admission transaction, the service recomputes the fixed F09o
evaluator output from the bound report; a synthetic or otherwise different
ProposalSet is `incompatible` and publishes no review state. A report or media
pointer that loses currentness returns `stale`.
Lineage checkpoints retain an attributable fixed-system audit transition. A
newer report makes an earlier case historical without rewriting it. Use a
clean rebuild and regenerate supported reports/ProposalSets for an older
database; there is no migration, decision, apply, or write command.

```sh
mechanical-librarian review-case-get --database reports.sqlite \
  --review-case-id review_case_<64-lowercase-hex-characters>
```

Get returns the immutable case and derived currentness without writing it.
Both commands validate the complete canonical typed result before printing
success. Earlier schemas are rejected under the clean-state policy.
