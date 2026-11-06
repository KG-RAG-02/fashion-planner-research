from __future__ import annotations

import json
import unittest
from pathlib import Path

from market_research_agent import MarketResearchAgent, MarketResearchContext


class SerializationTests(unittest.TestCase):
    def test_default_result_is_json_compatible_and_matches_schema_envelope(self) -> None:
        result = MarketResearchAgent().run(
            MarketResearchContext(request_id="req-123", planning_request={})
        )
        payload = result.to_dict()
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        schema_path = (
            Path(__file__).parents[1]
            / "contracts"
            / "schemas"
            / "market-research-result.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(set(decoded), set(schema["required"]))
        self.assertEqual(decoded["status"], "no_results")
        self.assertEqual(decoded["schema_version"], "1.0")
        self.assertEqual(decoded["agent_id"], "market-research-agent")


if __name__ == "__main__":
    unittest.main()
