# Project_07 contracts

Project_07 owns the schemas and examples for its local result and the shared HTTP envelope. Files under `schemas/proposals/` describe optional extensions and are not the v0.1 wire payload.

- `schemas/vogue-handoff-0.1-request.schema.json`
- `schemas/vogue-handoff-0.1-response.schema.json`
- `schemas/vogue-handoff-0.1-error.schema.json`
- `schemas/market-research-result.schema.json` (producer result 1.0)
- `schemas/proposals/` (non-normative proposals)

`schemas/proposals/vogue-orchestration-request-0.1-proposal.schema.json` describes the additive structured-input route at `/v1/agents/sales-market-agent/structured-runs`. It is not part of the agreed request schema for `/runs`, and requires cross-agent contract review before an orchestrator depends on it.

`schemas/proposals/sales-market-review-0.1-proposal.schema.json` and its response schema describe P07's local `POST /v1/agents/sales-market-agent/reviews` route. The synchronous response goes directly to the caller; the route does not push results to an orchestrator. The caller should be the orchestrator after a PlanningDraft exists. The route is not a cross-agent ratified contract, and connected Musinsa/fabric/customer sources are currently unavailable.

`schemas/proposals/apparel-costing-input-1.0-proposal.schema.json` defines explicit BOM, SAM-based labor, factory and landed-cost inputs. P07 calculates a margin only when the full costing sheet and a same-currency ex-tax proposed price are supplied. No default price, waste, efficiency, discount, return, cost, markup, duty or FX rate is invented.

When the loopback server starts with `--enable-synthetic-simulation`, the main UI at `/` submits to the stable `/v1/agents/sales-market-agent/runs` path. That response remains v0.1 and contains synthetic-labeled proposal findings; it does not include synthetic Evidence records because the stable Evidence contract only permits `permission_status=authorized`. For full observation/Evidence lineage and synthetic-only adjacent-period calculations, `schemas/proposals/synthetic-rag-analysis-0.2-proposal.schema.json` describes `/v1/agents/sales-market-agent/simulation-runs`; 0.1 is retained as a prior proposal and the legacy `/v1/simulations/synthetic-rag/runs` path is an alias to the current response. Both paths require the existing bearer token. Neither loopback UI presentation nor the stable route changes configure an externally deployed service.
