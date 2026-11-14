# P07 review prompt

`target_audience_hypothesis/v1/` is the only prompt used by the active `POST /v1/agents/sales-market-agent/reviews` flow. It receives the allowlisted product projection and summarized source evidence. The API sends this text to the model only when `VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED=true`.

- `system_template.md`: editable source prompt
- `rendered_system.md`: runtime prompt loaded by `llm/target_audience.py`
- `user_template.md`: human-readable input shape; runtime builds a reduced JSON payload directly
- `examples.json`: synthetic behavior examples; not injected into model calls

The prompt output is validated in `src/market_research_agent/llm/target_audience.py`. Keep its required fields synchronized with `_OUTPUT_FIELDS` and the prompt's JSON instructions.
