# Project_07 API contract

`POST /v1/agents/sales-market-agent/reviews` accepts one request envelope version:
`vogue-sales-market-review/0.3-proposal`.

- Request: [`sales-market-review-0.3-proposal.schema.json`](schemas/proposals/sales-market-review-0.3-proposal.schema.json) and [example](examples/sales-market-review-0.3-proposal.request.json)
- Successful response: [`sales-market-review-0.3-proposal-response.schema.json`](schemas/proposals/sales-market-review-0.3-proposal-response.schema.json) and [example](examples/sales-market-review-0.3-proposal.response.json)
- Request/service errors: [`sales-market-review-0.3-error.schema.json`](schemas/proposals/sales-market-review-0.3-error.schema.json)

The request contains the matching envelope and input `request_id`, a minimal
`product` projection, and one `product_evidence` entry for every supplied
product field. P07 rejects unknown fields and unsupported contract versions.
P08 sends only the product projection and its provenance, not the full Draft,
proposed price, costing sheet, or image references.

Successful responses preserve the same `request_id` in the envelope and result.
The envelope version is 0.3; the result's producer schema is
`sales-market-review/0.1-internal`. Result status is `ok`, `partial`, or
`no_results`. Errors use the same envelope version and an `error` object.

Each `result.evidence` entry includes a source-level `analysis` summary and
`supports_fields` paths. Result rows and derived analyses carry `evidence_ids`
that point back to those entries. H&M customer evidence contains aggregate age
bands only; price evidence records its sample and period; fabric evidence
identifies matched listing records; margin benchmark and FX inputs have
separate source entries. These links describe which input supported each
calculation and do not turn a benchmark into a product observation.

For request and response behavior, see the [P08 → P07 review flow](../docs/design/post-planning-sales-market-review.md).
