FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --uid 10001 agent

COPY --chown=agent:agent pyproject.toml README.md ./
COPY --chown=agent:agent src/ ./src/
COPY --chown=agent:agent contracts/ ./contracts/
COPY --chown=agent:agent prompts/ ./prompts/
COPY --chown=agent:agent examples/ ./examples/
COPY --chown=agent:agent scripts/ ./scripts/
COPY --chown=agent:agent tests/ ./tests/

RUN pip install --no-cache-dir .

USER agent

CMD ["python", "-m", "market_research_agent.runtime"]
