# Audiobook predecessor documents

These documents were produced by the public synthetic audiobook fixture at
`0a2a193a6645e3f7529f626ddf9abd957ae01251`, before the audiobook metadata change.
They preserve the actual report v43, proposal v7 and packet v8 bytes. Tests must
reject them through current consumers; never update their version strings or
recompute their identities to make them current.

`source-closure.json` binds the actual base source bytes and the three original
binding-assignment AST digests at that same full commit. `source-texts.json.gz`
contains deterministic gzip-compressed JSON with the base Audnexus and Policy
source texts (99,684 uncompressed bytes; 21,838 compressed bytes), needed to retain
the historical correction tests' exact reverse-patch assertions. These are inert
historical fixture data, not executable compatibility implementations. Current
successor tests separately bind the changed implementation and operation inventory.
