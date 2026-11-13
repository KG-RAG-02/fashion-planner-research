# Project_07 contracts

Project_07 owns the schemas and examples for its local result and the shared HTTP envelope. Files under `schemas/proposals/` describe optional extensions and are not the v0.1 wire payload.

- `schemas/vogue-handoff-0.1-request.schema.json`
- `schemas/vogue-handoff-0.1-response.schema.json`
- `schemas/vogue-handoff-0.1-error.schema.json`
- `schemas/market-research-result.schema.json` (producer result 1.0)
- `schemas/proposals/` (non-normative proposals)

`schemas/proposals/vogue-orchestration-request-0.1-proposal.schema.json` describes the additive structured-input route at `/v1/agents/sales-market-agent/structured-runs`. It is not part of the agreed request schema for `/runs`, and requires cross-agent contract review before an orchestrator depends on it.

`schemas/proposals/sales-market-review-0.2-proposal.schema.json` and its response schema describe P07's local `POST /v1/agents/sales-market-agent/reviews` route. The request accepts only `input.product.category` (required) and optional `product_type`, `gender`, `season`, `material`. Optional fields may be omitted or null; non-null strings must be non-empty. Gender is restricted to `women`, `men`, `unisex`, `unknown`. Price/currency, costing, image references, and P06 evidence metadata are rejected as unsupported. P06's review bridge owns extraction of the five allowed product attributes from its validated draft; P07 receives no full draft. The synchronous response goes directly to the P06 bridge. The response producer schema remains `sales-market-review/0.1-internal`; request/response envelope version is `vogue-sales-market-review/0.2-proposal`. The previous 0.1 input allowed draft metadata, price/currency, costing and image fields, so this is an intentionally breaking request contract change: callers must migrate to 0.2. Historical H&M Kaggle price and customer sources run only in explicit non-commercial research mode. The owner-provided fabric listing CSV is read from R2; its current unknown sales units do not support per-meter costing.

`schemas/proposals/apparel-costing-input-1.0-proposal.schema.json` remains a domain-level costing design artifact. That sheet is not accepted by the current `/reviews` wire contract; P06 must not forward it to this route.

When the loopback server starts with `--enable-synthetic-simulation`, the main UI at `/` submits to the stable `/v1/agents/sales-market-agent/runs` path. That response remains v0.1 and contains synthetic-labeled proposal findings; it does not include synthetic Evidence records because the stable Evidence contract only permits `permission_status=authorized`. For full observation/Evidence lineage and synthetic-only adjacent-period calculations, `schemas/proposals/synthetic-rag-analysis-0.2-proposal.schema.json` describes `/v1/agents/sales-market-agent/simulation-runs`; 0.1 is retained as a prior proposal and the legacy `/v1/simulations/synthetic-rag/runs` path is an alias to the current response. Both paths require the existing bearer token. Neither loopback UI presentation nor the stable route changes configure an externally deployed service.
