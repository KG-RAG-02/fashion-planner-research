# P07 target-audience prompt

`target_audience_hypothesis/v1/` supports one optional part of the active
`POST /v1/agents/sales-market-agent/reviews` flow: generating a target-audience
hypothesis. It is not the market-data analysis itself. P07 builds the prompt
input from the allowlisted product fields and summarized customer-age,
historical-price, and fabric-listing evidence. It does not include the full P06
Draft or customer-level source rows.

The code default allows this transfer. Set
`VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED=false` in the process environment to turn
it off. An enabled call needs `LLM_API_KEY`; missing credentials or a failed
call is reported as a nonfatal target-audience error, while other review
results are preserved. The generated output is a hypothesis, not an observed
customer fact.

- `system_template.md`: editable source prompt
- `rendered_system.md`: runtime system prompt loaded by `llm/target_audience.py`
- `user_template.md`: human-readable input shape; runtime builds a reduced JSON payload directly
- `examples.json`: synthetic behavior examples; not injected into model calls

The prompt output is validated in `src/fashion_planner_research/llm/target_audience.py`. Keep its required fields synchronized with `_OUTPUT_FIELDS` and the prompt's JSON instructions.
