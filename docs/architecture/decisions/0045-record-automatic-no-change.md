# ADR-0045: Record automatic no-change outcomes

Automatic candidates that canonically equal the persisted review-case target
are recorded as immutable `field_effect_no_changes` facts. They are terminal
decisions, not field-effect plans: they carry no consent, capability, authority
or write identity and are created before any external guard is consulted.

The exact-current schema deliberately uses a `WITHOUT ROWID` primary-key table
so its durable ledger adds no implicit SQLite autoindex.
