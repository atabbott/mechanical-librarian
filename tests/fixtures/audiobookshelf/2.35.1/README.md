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
