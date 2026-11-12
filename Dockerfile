FROM python:3.14-slim

# Read-only-rootfs friendly: no bytecode writes, HOME on /tmp (tmpfs), and
# writable state only under the declared /data paths (volumes or tmpfs).
# .env is never loaded in the container; configuration comes from the
# process environment (VOGUE_AGENT_SERVICE_BEARER_TOKEN is required).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    VOGUE_PROJECT07_DISABLE_DOTENV=1 \
    VOGUE_P07_REVIEW_ARTIFACT_DIR=/data/reviews \
    VOGUE_R2_HM_DB_CACHE_DIR=/data/hm-cache

WORKDIR /app

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin agent \
    && mkdir -p /data/reviews /data/hm-cache \
    && chown 10001:10001 /data/reviews /data/hm-cache \
    && chmod 0700 /data/reviews /data/hm-cache

COPY --chown=10001:10001 pyproject.toml README.md ./
COPY --chown=10001:10001 src/ ./src/
COPY --chown=10001:10001 contracts/ ./contracts/
COPY --chown=10001:10001 examples/ ./examples/
COPY --chown=10001:10001 scripts/ ./scripts/
COPY --chown=10001:10001 tests/ ./tests/

# r2 extra: boto3 + duckdb for the configured H&M R2 DuckDB source.
RUN pip install --no-cache-dir ".[r2]"

USER 10001:10001

EXPOSE 8766

# Liveness only: loopback /api/health, no auth, no external call, proxies ignored.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; r = u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8766/api/health', timeout=3); raise SystemExit(0 if r.status == 200 else 1)"]

# Bearer-protected service bind (conventions section 2). The idle development
# loop remains available as: python -m market_research_agent.runtime
CMD ["vogue-market-research-http", "--service-mode", "--host", "0.0.0.0", "--port", "8766"]
