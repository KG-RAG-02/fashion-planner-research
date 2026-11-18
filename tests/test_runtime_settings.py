"""정책 스위치는 기본으로 꺼져 있고, 명시한 값으로만 켜진다."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fashion_planner_research import runtime_settings

_SWITCHES = (
    ("VOGUE_HM_RESEARCH_NONCOMMERCIAL", runtime_settings.hm_research_noncommercial_enabled),
    ("VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED", runtime_settings.target_llm_transfer_authorized),
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
        with patch.dict(os.environ, {"VOGUE_USD_KRW_FALLBACK_RATE": " ", "VOGUE_USD_KRW_FALLBACK_DATE": ""}, clear=True):
            self.assertEqual(
                runtime_settings.usd_krw_fallback(),
                (runtime_settings.DEFAULT_USD_KRW_FALLBACK_RATE, runtime_settings.DEFAULT_USD_KRW_FALLBACK_DATE),
            )


if __name__ == "__main__":
    unittest.main()
