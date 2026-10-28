# Audiobook predecessor documents

These documents were produced by the public synthetic audiobook fixture at
`0a2a193a6645e3f7529f626ddf9abd957ae01251`, before the audiobook metadata change.
They preserve the actual report v43, proposal v7 and packet v8 bytes. Tests must
reject them through current consumers; never update their version strings or
recompute their identities to make them current.

`outcome.json` was produced by that same exact baseline wheel from a copy of
the retained synthetic predecessor database using `persist_shadow_outcome`.
It preserves genuine outcome v5 bytes for current nested sample-response refusal;
the evaluated timestamp belongs to the sample projection, not the outcome identity.
