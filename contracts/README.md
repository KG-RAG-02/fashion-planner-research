# Project_07 contracts

Project_07 owns the schemas and examples for its local result and the shared HTTP envelope. Files under `schemas/proposals/` describe optional extensions and are not the v0.1 wire payload.

- `schemas/vogue-handoff-0.1-request.schema.json`
- `schemas/vogue-handoff-0.1-response.schema.json`
- `schemas/vogue-handoff-0.1-error.schema.json`
- `schemas/market-research-result.schema.json` (producer result 1.0)
- `schemas/proposals/` (non-normative proposals)

`schemas/proposals/vogue-orchestration-request-0.1-proposal.schema.json` describes the additive structured-input route at `/v1/agents/sales-market-agent/structured-runs`. It is not part of the agreed request schema for `/runs`, and requires cross-agent contract review before an orchestrator depends on it.

`schemas/proposals/synthetic-rag-analysis-0.1-proposal.schema.json` describes the opt-in simulation response from the sales-market-agent API namespace at `/v1/agents/sales-market-agent/simulation-runs`. The main service UI is available at `/` when the loopback server starts with `--enable-synthetic-simulation`. The legacy `/v1/simulations/synthetic-rag/runs` path remains as an alias. Both operations require the existing bearer token and remain outside the stable v0.1 handoff contract; their same-origin UI presentation does not mean external production deployment is configured.
