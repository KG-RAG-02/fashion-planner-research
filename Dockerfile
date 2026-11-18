FROM python:3.14-slim

# Runtime state lives on declared volumes; the root filesystem can stay read-only.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    PORT=8766 \
    VOGUE_PROJECT07_DISABLE_DOTENV=1 \
    VOGUE_R2_HM_DB_CACHE_DIR=/data/hm-cache \
    VOGUE_HM_AGE_DUCKDB_TEMP_DIRECTORY=/data/hm-cache/duckdb-age-tmp \
    VOGUE_DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions

WORKDIR /app

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin agent \
    && mkdir -p /data/hm-cache /data/hm-cache/duckdb-age-tmp \
    && chown 10001:10001 /data/hm-cache /data/hm-cache/duckdb-age-tmp \
    && chmod 0700 /data/hm-cache /data/hm-cache/duckdb-age-tmp

COPY --chown=10001:10001 pyproject.toml README.md ./
COPY --chown=10001:10001 src/ ./src/
COPY --chown=10001:10001 contracts/ ./contracts/
COPY --chown=10001:10001 scripts/ ./scripts/
COPY --chown=10001:10001 tests/ ./tests/

RUN pip install --no-cache-dir '.[r2]' \
    && mkdir -p /opt/duckdb/extensions \
    && python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb/extensions'\"); c.execute('INSTALL httpfs'); c.execute('LOAD httpfs'); c.close()" \
    && chown -R 10001:10001 /opt/duckdb

USER 10001:10001

RUN python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb/extensions'\"); c.execute('LOAD httpfs'); assert c.execute(\"SELECT loaded FROM duckdb_extensions() WHERE extension_name='httpfs'\").fetchone()[0]; c.close()"

EXPOSE 8766

# Liveness uses loopback only and ignores proxy settings.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; r = u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8766/api/health', timeout=3); raise SystemExit(0 if r.status == 200 else 1)"]

CMD ["fashion-planner-research", "--service-mode", "--host", "0.0.0.0", "--port", "8766"]
