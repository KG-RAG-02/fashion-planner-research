"""한국어 카테고리 이름이 패키지의 카테고리 매핑으로 H&M 카테고리에 이어진다."""
from __future__ import annotations

import json
import unittest

from fashion_planner_research.resource_paths import CONFIGS_DIR
from fashion_planner_research.sources.sales.hm_kaggle.hm_customer_age_source import _mapped_requested_category


class CategoryMappingTests(unittest.TestCase):
    def setUp(self):
        self.mapping = json.loads((CONFIGS_DIR / "hm-category-map.json").read_text(encoding="utf-8"))

    def test_korean_minor_categories_reach_hm_categories(self):
        for label, expected in (
            ("스커트", "Bottoms"), ("팬츠", "Bottoms"), ("가디건", "Knitwear"),
            ("자켓", "Outerwear"), ("블라우스", "Tops"), ("점프수트", "One-piece"),
        ):
            with self.subTest(label=label):
                self.assertEqual(_mapped_requested_category(label, self.mapping), expected)

    def test_every_alias_targets_a_mapped_category(self):
        targets = (set(self.mapping["product_group_name"].values())
                   | set(self.mapping["product_type_name"].values()))
        aliases = set(self.mapping["requested_category_aliases"].values())
        self.assertEqual(sorted(aliases - targets), [])


if __name__ == "__main__":
    unittest.main()
