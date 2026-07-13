# Audiobookshelf 2.35.1 connection fixtures

These synthetic, versioned fixtures implement adapter contract
`abs-connection-v1` and fixture revision `abs-2.35.1-fixture-v1`. They retain
only the shape needed by F04 and contain no production identity, library, item,
content, endpoint, or credential value.

Evidence reconciled on 2026-07-13:

- official Audiobookshelf source for 2.35.1 identifies `GET /status` and
  observational `POST /api/authorize` returning the login-response profile;
- API keys introduced in 2.26.0 authenticate as Bearer JWTs;
- a complete profile explicitly grants unrestricted library, tag, and
  explicit-content visibility; selected private tag values are not retained;
- the public API reference is explicitly out of date and its authorize GET is
  not used as current contract evidence; and
- the disposable qualification image is pinned to
  `sha256:1eef6716183c52abafe5405e7d6be8390248ecd59c7488c44af871757ac8fc4d`.

Upstream source: <https://github.com/advplyr/audiobookshelf>
API reference: <https://api.audiobookshelf.org/>

The F05a cohort adds a synthetic libraries response, one two-item page, and
expanded audiobook/ebook details. Identifiers and paths are invented. The page
preserves the 2.35.1 controller's string echo of requested `limit` and `page`
values. The upstream tag object is
`71764d118a5f12466beeaf8f1d4d5e0370a199dc` and resolves to commit
`aacdcc47ec0b6fa778b84627c79deb7ee0cecd70`.

The expanded items reproduce the frozen serializers rather than a flattened
test shape: audio/ebook facts live on `AudioFile`/`EBookFile`, and filename,
extension, path, relative path, size, and timestamps live under
`FileMetadata`. The mixed-null `isbn`/`asin` pairs reproduce ordinary Book JSON
where one supported identifier is valid and its sibling is null. All identities,
names, paths, sizes, timestamps, and metadata values are synthetic.
