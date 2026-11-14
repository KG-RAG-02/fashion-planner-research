# Project_07 review API contract

P07 receives a small product projection, analyzes configured historical market,
customer aggregate, and fabric-listing evidence, then returns a synchronous
review. It reports evidence, missing sources, errors, and limitations so a
consumer can distinguish observations from estimates and hypotheses.

`POST /v1/agents/sales-market-agent/reviews` accepts one request envelope version:
`vogue-sales-market-review/0.3-proposal`.
The route requires `Authorization: Bearer <token>`. `GET /api/health` is a
liveness check and does not verify access to research data or providers.

- Request: [`sales-market-review-0.3-proposal.schema.json`](schemas/proposals/sales-market-review-0.3-proposal.schema.json) and [example](examples/sales-market-review-0.3-proposal.request.json)
- Successful response: [`sales-market-review-0.3-proposal-response.schema.json`](schemas/proposals/sales-market-review-0.3-proposal-response.schema.json) and [example](examples/sales-market-review-0.3-proposal.response.json)
- Request/service errors: [`sales-market-review-0.3-error.schema.json`](schemas/proposals/sales-market-review-0.3-error.schema.json)

The request contains the matching envelope and input `request_id`, a minimal
`product` projection, and one `product_evidence` entry for every supplied
product field. P07 rejects unknown fields and unsupported contract versions.
P08 validates the projection produced from its P06 bridge, then calls P07
directly. P06 does not call P07. P08 sends only the product projection and its
provenance, not the full Draft, proposed price, costing sheet, or image
references. The response does not attest to a Draft ID or hash.

Successful responses preserve the same `request_id` in the envelope and result.
The envelope version is 0.3; the result's producer schema is
`sales-market-review/0.1-internal`. Result status is `ok`, `partial`, or
`no_results`. Errors use the same envelope version and an `error` object.
`ok` means applicable configured sources yielded review results; `partial`
means results exist alongside a source limitation or failure; `no_results`
means no product-specific review evidence was available.

Each `result.evidence` entry includes a source-level `analysis` summary and
`supports_fields` paths. Result rows and derived analyses carry `evidence_ids`
that point back to those entries. H&M customer evidence contains aggregate age
bands only; price evidence records its sample and period; fabric evidence
identifies matched listing records; margin benchmark and FX inputs have
separate source entries. These links describe which input supported each
calculation and do not turn a benchmark into a product observation.

For request and response behavior, see the [P08 → P07 review flow](../docs/design/post-planning-sales-market-review.md).
