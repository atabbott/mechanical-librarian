# Metadata report v46 predecessor

Generated with the unmodified producer at
`47680a5ae09c60add72f4fd0c08cb76c6b2752d9` before the #1084 admission change.
A synthetic Google Books HTTP response states `Example\u200b Book` with a valid
ISBN, publisher and description. The base adapter admits the title as VALID;
the report round-trips and publishes/reopens in SQLite, but proposal evaluation
raises `ReportValueError: title is invalid`.

`report-v46.json` is that producer's canonical JSON plus a newline.
`reports-v46.sql` is SQLite's dump after actual repository publication.
These bytes remain historical fixtures, never relabelled as a current report.

SHA-256:

- `report-v46.json`: `f55be7704eab4b26c865fd8575b819ccd4a86e21cf98d3f865106817777ad0ff`
- `reports-v46.sql`: `1d3eff3cc98790e0634ac8f28e0a92ecec3adf5848a97e63ce122432878f41e8`
