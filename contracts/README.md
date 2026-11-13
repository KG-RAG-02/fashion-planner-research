# Project_07 active API contract

Only schemas used by the active `POST /v1/agents/sales-market-agent/reviews` endpoint are retained here:

- [`sales-market-review-0.2-proposal.schema.json`](schemas/proposals/sales-market-review-0.2-proposal.schema.json): request envelope and minimal P06 bridge payload.
- [`sales-market-review-0.2-proposal-response.schema.json`](schemas/proposals/sales-market-review-0.2-proposal-response.schema.json): successful review envelope.
- [`sales-market-review-0.2-error.schema.json`](schemas/proposals/sales-market-review-0.2-error.schema.json): request and service error envelope.
- [`sales-market-review-0.2-proposal.request.json`](examples/sales-market-review-0.2-proposal.request.json): request example.

The request accepts required `category` and optional `product_type`, `gender`, `season`, and `material`. The P06 review bridge extracts these fields from its validated product profile. P07 does not accept a full P06 draft, price/currency, costing, image references, or P06 evidence metadata. P08 receives P07's response through P06 and does not call P07 directly.

The request/response envelope version is `vogue-sales-market-review/0.2-proposal`; the producer result schema remains `sales-market-review/0.1-internal`. The former `/runs`, `/structured-runs`, and synthetic simulation schemas were removed with their retired routes. Domain serializers and analysis code are not wire contracts for this HTTP API.
