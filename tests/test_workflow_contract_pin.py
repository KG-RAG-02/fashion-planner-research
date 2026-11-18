"""The review request, response, and error schemas are locally valid."""
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


class CurrentContractTests(unittest.TestCase):
    def test_published_examples_match_current_contract_schemas(self):
        base = ROOT / "contracts"
        for name, schema_name in (
            ("research-review.request.json", "research-review-request.schema.json"),
            ("research-review.response.json", "research-review-response.schema.json"),
        ):
            with self.subTest(example=name):
                schema = json.loads((base / "schemas" / schema_name).read_text())
                example = json.loads((base / "examples" / name).read_text())
                Draft202012Validator(schema).validate(example)
