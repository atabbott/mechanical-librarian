FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN UV_PROJECT_ENVIRONMENT=/opt/mechanical-librarian uv sync --frozen --no-dev --no-editable

FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8
RUN groupadd --system --gid 65532 librarian \
    && useradd --system --uid 65532 --gid librarian --home-dir /nonexistent --shell /usr/sbin/nologin librarian \
    && install -d -o librarian -g librarian -m 0700 /data
COPY --from=build /opt/mechanical-librarian /opt/mechanical-librarian
USER 65532:65532
EXPOSE 8443
VOLUME ["/data"]
ENTRYPOINT ["/opt/mechanical-librarian/bin/mechanical-librarian-container"]
