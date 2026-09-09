# The audiobook inspector parses no media bytes itself: it runs `ffprobe` as an
# isolated, resource-bounded child. That probe is built here from a
# checksum-pinned upstream release rather than installed from the distribution,
# for three reasons. Network support is compiled out, so the slice's
# zero-network invariant is a property of the binary rather than of the sandbox
# around it. The enabled set is exactly the two admitted containers and the
# codecs they carry, so a payload outside that envelope is refused as an
# extraction failure instead of being demuxed by a component the library never
# reads -- `ffprobe` sniffs by content, not by extension, and the distribution
# package's `libavfilter` and `libavdevice` closure, which drags in LLVM, Mesa,
# and X11, never exists at all. In
# exchange this project owns the pin: a CVE in FFmpeg is answered by bumping
# `FFMPEG_VERSION` and `FFMPEG_SHA256` here, not by a base-image rebuild.
FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS probe-build
ARG FFMPEG_VERSION=7.1.5
ARG FFMPEG_SHA256=de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates curl gcc libc6-dev make pkg-config xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSLO "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    && echo "${FFMPEG_SHA256}  ffmpeg-${FFMPEG_VERSION}.tar.xz" | sha256sum -c - \
    && tar xf "ffmpeg-${FFMPEG_VERSION}.tar.xz"
WORKDIR /src/ffmpeg-${FFMPEG_VERSION}
# `--disable-x86asm` keeps an assembler out of the build's supply chain: reading
# container headers uses no SIMD decode path, and the flag is a no-op on arm64,
# so both architectures build from the same inputs.
RUN ./configure \
        --disable-everything --disable-network --disable-autodetect --disable-doc \
        --disable-avdevice --disable-avfilter --disable-swscale --disable-swresample \
        --disable-postproc --disable-encoders --disable-muxers --disable-filters \
        --disable-indevs --disable-outdevs --disable-hwaccels --disable-bsfs \
        --disable-ffmpeg --disable-ffplay \
        --disable-x86asm \
        --enable-ffprobe \
        --enable-demuxer=mov,mp3 \
        --enable-decoder=aac,mp3,mjpeg,png \
        --enable-parser=aac,mpegaudio,mjpeg,png \
        --enable-protocol=file,pipe,fd \
    && make -j"$(nproc)" ffprobe \
    && strip ffprobe \
    && install -m 0755 ffprobe /usr/local/bin/ffprobe

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
    /reviewer/bin/bwrap --version > /reviewer/metadata/bubblewrap-version.txt
ADD --checksum=sha256:f0513d22bd932c53dd4eb267cc30d24b75c6dd6a72865178f092fb8913b9d6f1 \
    --chmod=0444 \
    https://codeload.github.com/openai/codex/tar.gz/be6e8eac029b183056b7e4402879f15d2c85f61b \
    /reviewer/source/codex-be6e8eac029b183056b7e4402879f15d2c85f61b.tar.gz
RUN set -eu; \
    source_root="codex-be6e8eac029b183056b7e4402879f15d2c85f61b"; \
    tar -xOf /reviewer/source/codex-be6e8eac029b183056b7e4402879f15d2c85f61b.tar.gz \
      "$source_root/LICENSE" > /reviewer/LICENSE.codex; \
    tar -xOf /reviewer/source/codex-be6e8eac029b183056b7e4402879f15d2c85f61b.tar.gz \
      "$source_root/codex-rs/vendor/bubblewrap/COPYING" > /reviewer/LICENSE.bubblewrap; \
    chmod 0555 /reviewer/source; \
    chmod 0444 /reviewer/LICENSE.codex /reviewer/LICENSE.bubblewrap; \
    echo "d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc  /reviewer/LICENSE.codex" | sha256sum -c -; \
    echo "b7993225104d90ddd8024fd838faf300bea5e83d91203eab98e29512acebd69c  /reviewer/LICENSE.bubblewrap" | sha256sum -c -; \
    cd /reviewer; \
    sha256sum \
      bin/codex \
      bin/bwrap \
      LICENSE.codex \
      LICENSE.bubblewrap \
      source/codex-be6e8eac029b183056b7e4402879f15d2c85f61b.tar.gz \
      > metadata/SHA256SUMS

FROM python:3.14.7-slim-bookworm@sha256:416f0db2a2b561945630cef9877a7ea0581b27449eb9fd9df42f03e1b74b5b63 AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml setup.py uv.lock README.md LICENSE ./
COPY scripts/build_frontend.py ./scripts/build_frontend.py
COPY scripts/verify_contained_reviewer_tool_inventory.py ./scripts/verify_contained_reviewer_tool_inventory.py
COPY src ./src
COPY --from=frontend-build /build/frontend/dist /frontend-dist
RUN ML_FRONTEND_PREBUILT_DIR=/frontend-dist \
    UV_PROJECT_ENVIRONMENT=/opt/mechanical-librarian \
    uv sync --frozen --no-dev --no-editable
COPY --from=reviewer-build /reviewer /reviewer-proof
RUN /opt/mechanical-librarian/bin/python \
    scripts/verify_contained_reviewer_tool_inventory.py \
    /reviewer-proof/bin/codex

FROM python:3.14.7-slim-bookworm@sha256:416f0db2a2b561945630cef9877a7ea0581b27449eb9fd9df42f03e1b74b5b63
COPY --from=probe-build /usr/local/bin/ffprobe /usr/local/bin/ffprobe
RUN /usr/local/bin/ffprobe -version \
    && groupadd --system --gid 65532 librarian \
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
