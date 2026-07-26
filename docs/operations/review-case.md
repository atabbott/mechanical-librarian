# Review-case admission

Admit one exact current ProposalSet:

```sh
mechanical-librarian review-case-admit --config config.json --database reports.sqlite \
  --artifact-store artifacts --proposal-set-id proposal_set_<64-lowercase-hex-characters>
```

The typed canonical result is `admitted`, `superseded`, `no_case`, `retired`,
or `stale`. Only `propose_change` and `unresolved_choice` results create a
case; `no_case` and `retired` have no empty case. Untrusted failures are
redacted as fixed `invalid`, `incompatible`, `currentness`, or `persistence`
codes.

The complete case binds the exact ProposalSet, report, target and file identity.
Lineage checkpoints retain an attributable fixed-system audit transition. A
newer report makes an earlier case historical without rewriting it. Use a
clean rebuild and regenerate supported reports/ProposalSets for an older
database; there is no migration, decision, apply, or write command.

```sh
mechanical-librarian review-case-get --database reports.sqlite \
  --review-case-id review_case_<64-lowercase-hex-characters>
```

Get returns the immutable case and derived currentness without writing it.
Earlier schemas are rejected under the clean-state policy.
