FROM node:24.19.0-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci --ignore-scripts --no-audit --no-fund
COPY frontend ./frontend
COPY docs/reference/review-openapi-v1.json ./docs/reference/review-openapi-v1.json
COPY src/mechanical_librarian/interfaces/web/settings_vocabulary.json ./src/mechanical_librarian/interfaces/web/settings_vocabulary.json
RUN cd frontend \
    && test "$(node --version)" = "v24.19.0" \
    && test "$(npm --version)" = "11.17.0" \
    && npm run generate \
    && npm run typecheck \
    && npm run build

FROM python:3.14.7-slim-bookworm@sha256:416f0db2a2b561945630cef9877a7ea0581b27449eb9fd9df42f03e1b74b5b63 AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml setup.py uv.lock README.md LICENSE ./
COPY scripts/build_frontend.py ./scripts/build_frontend.py
COPY src ./src
COPY --from=frontend-build /build/frontend/dist /frontend-dist
RUN ML_FRONTEND_PREBUILT_DIR=/frontend-dist \
    UV_PROJECT_ENVIRONMENT=/opt/mechanical-librarian \
    uv sync --frozen --no-dev --no-editable

FROM python:3.14.7-slim-bookworm@sha256:416f0db2a2b561945630cef9877a7ea0581b27449eb9fd9df42f03e1b74b5b63
RUN groupadd --system --gid 65532 librarian \
    && useradd --system --uid 65532 --gid librarian --home-dir /nonexistent --shell /usr/sbin/nologin librarian \
    && install -d -o librarian -g librarian -m 0700 /data
COPY --from=build /opt/mechanical-librarian /opt/mechanical-librarian
USER 65532:65532
EXPOSE 8443
VOLUME ["/data"]
# The image carries no curl and no wget, and gains neither: the probe is the
# installed interpreter asking the server over loopback. `start-period` covers
# the first-run store creation the entrypoint does before the server binds.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --start-interval=5s \
    --retries=3 \
    CMD ["/opt/mechanical-librarian/bin/mechanical-librarian-container-health"]
ENTRYPOINT ["/opt/mechanical-librarian/bin/mechanical-librarian-container"]
