"""Korean Draft categories reach the English H&M taxonomy through an explicit alias table."""
import unittest

from market_research_agent.domain.models import MarketResearchContext, ResultStatus, SourceReport
from market_research_agent.sources.sales.hm_kaggle import hm_sales_adapter as adapter


class _Capture(adapter.HmHistoricalSalesSource):
    def __init__(self, category_map):
        self.category_map = category_map
        self.seen = []

    def _research(self, context):
        self.seen.append(context.requested_category)
        return SourceReport(status=ResultStatus.NO_RESULTS, limitations=("delegated",))


def _context(category):
    return MarketResearchContext(request_id="req-1", planning_request={"query": "q"}, requested_category=category)


class CategoryAliasTest(unittest.TestCase):
    MAP = {"Skirt": "Bottoms", "Sweater": "Knitwear", "Coat": "Outerwear"}

    def test_korean_label_maps_to_canonical_target_with_visible_limitation(self):
        source = _Capture(self.MAP)
        report = source.research(_context("스커트"))
        self.assertEqual(source.seen, ["Bottoms"])
        self.assertIn("'스커트'", report.limitations[0])
        self.assertIn("'Bottoms'", report.limitations[0])
        self.assertIn(adapter.CATEGORY_ALIASES_VERSION, report.limitations[0])
        self.assertEqual(report.limitations[1:], ("delegated",))

    def test_exact_canonical_category_is_untouched(self):
        source = _Capture(self.MAP)
        report = source.research(_context("Bottoms"))
        self.assertEqual(source.seen, ["Bottoms"])
        self.assertEqual(report.limitations, ("delegated",))

    def test_unknown_label_keeps_exact_match_behaviour(self):
        source = _Capture(self.MAP)
        source.research(_context("모자"))
        self.assertEqual(source.seen, ["모자"])

    def test_alias_is_not_used_when_the_target_is_not_mapped(self):
        source = _Capture({"Skirt": "Bottoms"})
        source.research(_context("니트웨어"))
        self.assertEqual(source.seen, ["니트웨어"])

    def test_every_alias_target_exists_in_the_shipped_category_map(self):
        import json
        from pathlib import Path
        shipped = json.loads((Path(adapter.__file__).resolve().parents[3] / "resources/configs/hm-category-map.v1.json").read_text(encoding="utf-8"))
        targets = set(shipped["product_group_name"].values()) | set(shipped["product_type_name"].values())
        self.assertEqual(sorted(set(adapter.KOREAN_CATEGORY_ALIASES.values()) - targets), [])


if __name__ == "__main__":
    unittest.main()
