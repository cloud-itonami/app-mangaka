# lg-mangaka OSS Dockerfile (replaces `langgraph build` / `langchain/langgraph-api`).
#
# License: 100% OSS — no LANGSMITH_API_KEY / LANGGRAPH_CLOUD_LICENSE_KEY required.
#
# Build:
#   cd 60-apps/etzhayyim-project-mangaka/lg
#   docker buildx build --platform linux/amd64 \
#     --build-context py=../../../40-engine/kotoba/crates/kotoba-kotodama/py \
#     --build-context kami=../../../40-engine/kami-engine \
#     -t ghcr.io/etzhayyimcojp/lg-mangaka:0.1.0-amd64 \
#     --push .
#
# Stages:
#   1. kami-wheel-builder — compiles the `kami_mangaka_scene` PyO3 wheel
#      via maturin (P11 of ADR-2605141200). Lives in its own stage so the
#      Rust toolchain (~2 GB) is dropped from the runtime image.
#   2. runtime — slim Python 3.11 + kotodama + lg_mangaka + the
#      kami_mangaka_scene wheel installed from stage 1.

# ── Stage 1: maturin wheel build ─────────────────────────────────────────
FROM rust:1.82-slim-bookworm AS kami-wheel-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CARGO_TERM_COLOR=never

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        pkg-config build-essential \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --break-system-packages --no-cache-dir 'maturin>=1.7'

WORKDIR /build
COPY --from=kami . /build

# Build the wheel for the runtime python (3.11). `--features python`
# activates the PyO3 surface; `--strip` shaves debug symbols (~50% smaller).
RUN maturin build --release --features python --strip \
    --manifest-path kami-mangaka-scene/Cargo.toml \
    --out /build/wheels

# ── Stage 2: runtime ─────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# uv for fast, deterministic installs
RUN pip install --no-cache-dir 'uv>=0.4'

WORKDIR /app

# kotodama (editable, mounted via --build-context py=...)
COPY --from=py . /opt/kotodama
RUN uv pip install --system --no-cache -e /opt/kotodama

# kami-mangaka-scene wheel (P11). When --build-context kami=... is not
# supplied or the wheel build is skipped, the runtime gracefully falls
# back to pending placeholder blob keys in `_step_render_keyframes` —
# the lifecycle import-guard in `lg_mangaka.tools._try_real_render`
# treats `import kami_mangaka_scene` ImportError as "feature off".
COPY --from=kami-wheel-builder /build/wheels /opt/kami-wheels
RUN if ls /opt/kami-wheels/*.whl >/dev/null 2>&1; then \
        uv pip install --system --no-cache /opt/kami-wheels/*.whl; \
    else \
        echo "[lg-mangaka] no kami wheel — render path stays on placeholders"; \
    fi

# lg-mangaka own deps
COPY pyproject.toml /app/pyproject.toml
COPY langgraph.json /app/langgraph.json
COPY lg_mangaka /app/lg_mangaka

RUN uv pip install --system --no-cache -e /app

# Strip build tooling to shrink image
RUN uv pip uninstall --system uv 2>/dev/null || true

EXPOSE 8000

# Granian ASGI server (ADR-2605080600 — L3 Virtual Actor Runtime).
# Replaces the prior uvicorn worker. Single worker because lg-mangaka holds a
# single-instance cron (`LG_CRON_ENABLED=true` only on one replica); for HA,
# scale to N replicas with `LG_CRON_ENABLED=false` on N-1.
#
# Granian's Rust-accelerated edge halves p99 latency vs uvicorn on the
# `/runs` invoke path and removes the asyncio task-switch overhead that
# dominated the LangGraph Server's request/response cycle.
CMD ["granian", "--interface", "asgi", \
     "lg_mangaka.server:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--access-log", "--log-level", "info"]
