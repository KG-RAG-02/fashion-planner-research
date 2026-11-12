from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_research_agent.cli import main
from market_research_agent.sources.sales.hm_kaggle.hm_r2_duckdb_source import R2DuckDBHmHistoricalSalesSource


class HmCliRoutingTests(unittest.TestCase):
    def test_cli_auto_selects_shared_r2_db_key_before_legacy_csv_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            request_path = Path(temporary) / "request.json"
            request_path.write_text(json.dumps({
                "planning_request": {
                    "request_id": "synthetic-cli-routing-test",
                    "schema_version": "1.0",
                    "query": "Outerwear purchase rows",
                }
            }), encoding="utf-8")
            output = io.StringIO()
            args = [
                "--inputs-json", str(request_path),
                "--hm-category-map", "Jacket=Outerwear",
                "--hm-mapping-version", "synthetic-map-v1",
                "--proposal-v1-1",
            ]
            with patch.dict(os.environ, {
                "R2_DB_KEY": "shared.duckdb",
                "PROJECT07_HM_R2_PREFIX": "example/hm",
                "PROJECT07_HM_R2_MANIFEST_KEY": "example/hm/bundle.json",
            }, clear=True), patch(
                "market_research_agent.cli.R2DuckDBHmHistoricalSalesSource",
                wraps=R2DuckDBHmHistoricalSalesSource,
            ) as duckdb_source, patch(
                "market_research_agent.cli.R2HmHistoricalSalesSource",
            ) as csv_source, contextlib.redirect_stdout(output):
                exit_code = main(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(duckdb_source.call_args.kwargs["object_key"], "shared.duckdb")
        csv_source.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["sales_data"]["status"], "error")

    def test_cli_can_explicitly_force_local_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            request_path = Path(temporary) / "request.json"
            request_path.write_text(json.dumps({
                "planning_request": {
                    "request_id": "synthetic-cli-local-test",
                    "schema_version": "1.0",
                    "query": "Outerwear purchase rows",
                }
            }), encoding="utf-8")
            output = io.StringIO()
            args = [
                "--inputs-json", str(request_path),
                "--hm-source", "local",
                "--hm-processed-dir", str(Path(temporary) / "empty"),
                "--hm-category-map", "Jacket=Outerwear",
                "--hm-mapping-version", "synthetic-map-v1",
                "--proposal-v1-1",
            ]
            with patch.dict(os.environ, {"R2_DB_KEY": "shared.duckdb"}, clear=True), patch(
                "market_research_agent.cli.R2DuckDBHmHistoricalSalesSource",
            ) as duckdb_source, contextlib.redirect_stdout(output):
                exit_code = main(args)
        self.assertEqual(exit_code, 0)
        duckdb_source.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["sales_data"]["status"], "partial")


if __name__ == "__main__":
    unittest.main()
