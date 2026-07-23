# 0.4 domain model

`MetadataReport` is one closed typed observation of one explicitly selected
EPUB. It contains:

- target connection, library, item, and exact current title;
- selected file index, opaque file identity, EPUB format, and content digest;
- stable source-attributed local title and ISBN claims;
- a local `candidate` or `no_candidate` status with bounded reasons; and
- independent provider observations with stable input references and zero or
  more provider-identity-ordered candidates.

Title and ISBN are the only claim variants. ISBN claims retain the observed
substring, canonical checksum-valid identifier when available, at most 280
characters of directly supporting opening-matter context,
ebook/print/unknown/ambiguous applicability, and an explicit nonnumeric label
basis. Invalid, unlabeled, and conflicting-label identifiers remain distinct
facts; an ISBN-10 is not represented as an observed ISBN-13.

Provider observations are `disabled`, `insufficient_input`, `candidates`,
`no_match`, `authentication`, `authorization`, `rate_limited`, `unavailable`,
or `malformed`. Candidate claims record agreement, disagreement, incompatible,
or not-comparable facts against stable local claim references. No outcome is a
decision or winner.

Claim references and the report ID are derived from canonical content. Exact
replay therefore preserves IDs and bytes without an idempotency subsystem.
Every admitted EPUB occurrence, including identical values, retains source/DOM
order through a bounded v2 occurrence ordinal in its claim identity.
