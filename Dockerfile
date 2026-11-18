FROM python:3.14-slim AS base

# 실행 상태는 선언한 볼륨에만 쓰므로 루트 파일시스템은 읽기 전용으로 둘 수 있다.
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

# 실행에 필요한 것은 패키지(설정·프롬프트 포함)뿐이다.
COPY --chown=10001:10001 pyproject.toml README.md ./
COPY --chown=10001:10001 src/ ./src/

# httpfs 확장은 빌드 때 설치해 요청 처리 중에는 확장을 내려받지 않는다.
RUN pip install --no-cache-dir '.[r2]' \
    && mkdir -p /opt/duckdb/extensions \
    && python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb/extensions'\"); c.execute('INSTALL httpfs'); c.execute('LOAD httpfs'); c.close()" \
    && chown -R 10001:10001 /opt/duckdb

USER 10001:10001

RUN python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb/extensions'\"); c.execute('LOAD httpfs'); assert c.execute(\"SELECT loaded FROM duckdb_extensions() WHERE extension_name='httpfs'\").fetchone()[0]; c.close()"


# 오프라인 단위 테스트용 단계: docker compose --profile test run --rm research-tests
FROM base AS test

COPY --chown=10001:10001 contracts/ ./contracts/
COPY --chown=10001:10001 scripts/run_tests.py ./scripts/run_tests.py
COPY --chown=10001:10001 tests/ ./tests/

CMD ["python", "scripts/run_tests.py"]


# 기본 빌드 대상: 리뷰 서비스
FROM base AS runtime

EXPOSE 8766

# 상태 점검은 루프백만 쓰고 프록시 설정을 무시한다.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; r = u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8766/api/health', timeout=3); raise SystemExit(0 if r.status == 200 else 1)"]

CMD ["fashion-planner-research", "--service-mode", "--host", "0.0.0.0", "--port", "8766"]
