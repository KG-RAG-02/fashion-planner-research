"""정책 스위치는 기본으로 꺼져 있고, 명시한 값으로만 켜진다."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import tempfile
from pathlib import Path

from fashion_planner_research import runtime_settings
from fashion_planner_research.http_server import _load_project_r2_env

_SWITCHES = (
    ("RESEARCH_HM_NONCOMMERCIAL_RESEARCH", runtime_settings.hm_research_noncommercial_enabled),
    ("RESEARCH_TARGET_LLM_TRANSFER_AUTHORIZED", runtime_settings.target_llm_transfer_authorized),
)


class PolicySwitchTests(unittest.TestCase):
    def test_switches_are_off_when_unset_or_blank(self):
        for name, read in _SWITCHES:
            for env in ({}, {name: ""}, {name: "   "}):
                with self.subTest(name=name, env=env), patch.dict(os.environ, env, clear=True):
                    self.assertFalse(read())

    def test_only_explicit_true_values_turn_switches_on(self):
        for name, read in _SWITCHES:
            for value, expected in (("true", True), ("TRUE", True), ("1", True), ("yes", True),
                                    ("false", False), ("0", False), ("on", False)):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}, clear=True):
                    self.assertIs(read(), expected)

    def test_blank_fx_fallback_values_keep_the_dated_default(self):
        with patch.dict(os.environ, {"RESEARCH_USD_KRW_FALLBACK_RATE": " ", "RESEARCH_USD_KRW_FALLBACK_DATE": ""}, clear=True):
            self.assertEqual(
                runtime_settings.usd_krw_fallback(),
                (runtime_settings.DEFAULT_USD_KRW_FALLBACK_RATE, runtime_settings.DEFAULT_USD_KRW_FALLBACK_DATE),
            )



class DotenvLoaderTests(unittest.TestCase):
    def test_only_allowed_keys_are_loaded_and_the_process_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "# 주석\n"
                "export FASHION_PLANNER_SERVICE_TOKEN='from-dotenv'\n"
                "RESEARCH_HM_NONCOMMERCIAL_RESEARCH=true\n"
                "RESEARCH_REVIEW_SOURCE=synthetic # 합성\n"
                "R2_BUCKET=example-bucket\n"
                "LLM_MODEL=example-model\n"
                "RESEARCH_DISABLE_DOTENV=1\n"
                "PATH=/should/not/load\n"
                "OTHER_SERVICE_TOKEN=nope\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"PATH": "/usr/bin", "R2_BUCKET": "from-process"}, clear=True):
                _load_project_r2_env(env_file)
                loaded = dict(os.environ)
        self.assertEqual(loaded["FASHION_PLANNER_SERVICE_TOKEN"], "from-dotenv")
        self.assertEqual(loaded["RESEARCH_HM_NONCOMMERCIAL_RESEARCH"], "true")
        self.assertEqual(loaded["RESEARCH_REVIEW_SOURCE"], "synthetic")
        self.assertEqual(loaded["LLM_MODEL"], "example-model")
        self.assertEqual(loaded["R2_BUCKET"], "from-process")
        self.assertEqual(loaded["PATH"], "/usr/bin")
        self.assertNotIn("RESEARCH_DISABLE_DOTENV", loaded)
        self.assertNotIn("OTHER_SERVICE_TOKEN", loaded)

    def test_disable_switch_skips_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("RESEARCH_REVIEW_SOURCE=synthetic\n", encoding="utf-8")
            with patch.dict(os.environ, {"RESEARCH_DISABLE_DOTENV": "1"}, clear=True):
                _load_project_r2_env(env_file)
                self.assertNotIn("RESEARCH_REVIEW_SOURCE", os.environ)


if __name__ == "__main__":
    unittest.main()
