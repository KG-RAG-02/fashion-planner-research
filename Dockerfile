FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8766 \
    PROJECT07_PROMPTS_DIR=/app/prompts \
    PROJECT07_CONFIGS_DIR=/app/configs \
    VOGUE_DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb/extensions

WORKDIR /app

RUN useradd --create-home --uid 10001 agent

COPY --chown=agent:agent pyproject.toml README.md ./
COPY --chown=agent:agent src/ ./src/
COPY --chown=agent:agent contracts/ ./contracts/
COPY --chown=agent:agent prompts/ ./prompts/
COPY --chown=agent:agent configs/ ./configs/
COPY --chown=agent:agent tests/ ./tests/

RUN pip install --no-cache-dir '.[r2]' \
    && mkdir -p /opt/duckdb/extensions \
    && python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb/extensions'\"); c.execute('INSTALL httpfs'); c.execute('LOAD httpfs'); print(duckdb.__version__, c.execute(\"SELECT extension_name, installed, loaded FROM duckdb_extensions() WHERE extension_name='httpfs'\").fetchone()); c.close()" \
    && chown -R agent:agent /opt/duckdb

USER agent

EXPOSE 8766

RUN python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb/extensions'\"); c.execute('LOAD httpfs'); assert c.execute(\"SELECT loaded FROM duckdb_extensions() WHERE extension_name='httpfs'\").fetchone()[0]; c.close()"

CMD ["python", "-m", "market_research_agent.http_server", "--service-mode", "--host", "0.0.0.0", "--port", "8766"]
