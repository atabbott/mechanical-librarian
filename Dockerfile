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

FROM node:24.19.0-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS reviewer-build
ARG TARGETARCH
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) \
        package_version="0.147.0-linux-x64"; \
        archive="openai-codex-0.147.0-linux-x64.tgz"; \
        triple="x86_64-unknown-linux-musl"; \
        expected_sha256="c969740cf8297e4c31905cd551efeb2c99af5080c12c236bdf825598b250139a" \
        ;; \
      arm64) \
        package_version="0.147.0-linux-arm64"; \
        archive="openai-codex-0.147.0-linux-arm64.tgz"; \
        triple="aarch64-unknown-linux-musl"; \
        expected_sha256="4d09bf000597768dfa070b0e7c612f0fc258ac7fab364e570104ea625f918546" \
        ;; \
      *) echo "unsupported reviewer architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    mkdir -p /tmp/codex /reviewer/bin /reviewer/metadata; \
    npm pack "@openai/codex@$package_version" \
      --ignore-scripts --no-audit --no-fund --pack-destination /tmp/codex \
      >/dev/null; \
    echo "$expected_sha256  /tmp/codex/$archive" | sha256sum -c -; \
    tar -xzf "/tmp/codex/$archive" -C /tmp/codex; \
    test "$(node -p "require('/tmp/codex/package/package.json').version")" = "$package_version"; \
    test "$(node -p "require('/tmp/codex/package/vendor/$triple/codex-package.json').version")" = "0.147.0"; \
    install -m 0555 "/tmp/codex/package/vendor/$triple/bin/codex" /reviewer/bin/codex; \
    install -m 0555 "/tmp/codex/package/vendor/$triple/codex-resources/bwrap" /reviewer/bin/bwrap; \
    install -m 0444 /tmp/codex/package/package.json /reviewer/metadata/npm-package.json; \
    install -m 0444 "/tmp/codex/package/vendor/$triple/codex-package.json" /reviewer/metadata/codex-package.json; \
    test "$(/reviewer/bin/codex --version)" = "codex-cli 0.147.0"; \
    /reviewer/bin/bwrap --version > /reviewer/metadata/bubblewrap-version.txt; \
    cd /reviewer; \
    sha256sum bin/codex bin/bwrap > metadata/SHA256SUMS
ADD --checksum=sha256:d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc \
    --chmod=0444 \
    https://raw.githubusercontent.com/openai/codex/rust-v0.147.0/LICENSE \
    /reviewer/LICENSE.codex

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
COPY --from=reviewer-build /reviewer /opt/mechanical-librarian/reviewer
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
