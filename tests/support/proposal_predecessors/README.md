Synthetic proposal emitted by the actual `fixed-conservative-v25` producer at
`c452fd3cfd4b9c31cc469ccc1f00ad859120ee57`. The supported recording WORK subtitle
was omitted by that producer. Preserve these bytes as predecessor evidence.

`fixed-conservative-v32.json` is a compact capsule emitted before changes by
`85cd6578323f6868612697493a0240f603e80008`, using the named synthetic report
constructor and real repository publication/evaluation. It preserves the exact
proposal document, its digest, report identity/digest, and actual `sqlite_master`
proposal table DDL. Tests reconstruct only that baseline table over unchanged
current scaffolding; this is not a full historical database snapshot.

`fixed-conservative-v33.json` is the same compact capsule emitted before #1252 by
`cd82592055ecaf9fdf727cc2f9c5b607e7f1f92c`, from the same constructor, with the
current report schema unchanged. Preserve these bytes as predecessor evidence.

`fixed-conservative-v34.json` is the same compact capsule emitted before #1253 by
`b34ece634b208397c821f2e0fe99492453334e41`, from the same constructor, with the
current report schema unchanged. Preserve these bytes as predecessor evidence.
