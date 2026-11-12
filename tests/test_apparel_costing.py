from __future__ import annotations

import unittest

from market_research_agent.domain.apparel_costing import (
    CostingInputError,
    calculate_apparel_margin,
    validate_apparel_costing,
)


def costing_input():
    return {
        "schema_version": "1.0",
        "currency": "KRW",
        "price_basis": "ex_tax",
        "materials": [
            {
                "kind": "fabric", "name": "test woven", "unit_cost": 1000,
                "consumption": 2, "unit": "m", "waste_rate": 0.1,
                "source_ref": "test:fabric-quote",
            },
            {
                "kind": "trims", "name": "test buttons", "unit_cost": 100,
                "consumption": 1, "unit": "piece", "waste_rate": 0.1,
                "source_ref": "test:trim-quote",
            },
        ],
        "labor": {
            "sam_minutes": 20, "working_minute_cost": 50,
            "efficiency_rate": 0.8, "source_ref": "test:factory-cost-sheet",
        },
        "factory_costs": {
            "processing": {"amount": 200, "source_ref": "test:process-quote"},
            "finishing": {"amount": 100, "source_ref": "test:finish-quote"},
            "packaging": {"amount": 80, "source_ref": "test:packaging-quote"},
            "inspection": {"amount": 50, "source_ref": "test:inspection-quote"},
        },
        "factory_markup_rate": 0.1,
        "logistics_costs": {
            "freight": {"amount": 500, "source_ref": "test:freight-quote"},
            "insurance": {"amount": 20, "source_ref": "test:insurance-quote"},
            "duty": {"amount": 0, "source_ref": "test:duty-rate"},
            "inland_logistics": {"amount": 100, "source_ref": "test:inland-quote"},
        },
        "discount_rate": 0.1,
        "return_rate": 0.05,
        "target_gross_margin_rate": 0.5,
        "rate_source_refs": {
            "factory_markup_rate": "test:factory-policy",
            "discount_rate": "test:promotion-plan",
            "return_rate": "test:return-assumption",
            "target_gross_margin_rate": "test:margin-target",
        },
    }


class ApparelCostingTests(unittest.TestCase):
    def test_bom_sam_fob_landed_gross_margin_is_reproducible(self):
        result = calculate_apparel_margin(
            proposed_price=10000,
            price_currency="KRW",
            costing=costing_input(),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["formula_id"], "apparel-bom-sam-landed-gm/1.0")
        self.assertEqual(result["calculation"]["material_cost"], 2310)
        self.assertEqual(result["labor"]["labor_cost"], 1250)
        self.assertEqual(result["calculation"]["production_cost_before_markup"], 3990)
        self.assertEqual(result["calculation"]["fob_quote"], 4389)
        self.assertEqual(result["calculation"]["landed_cost"], 5009)
        self.assertEqual(result["calculation"]["net_sales_per_unit"], 8550)
        self.assertEqual(result["calculation"]["gross_profit_per_unit"], 3541)
        self.assertEqual(result["calculation"]["gross_margin_rate"], 0.414152)
        self.assertEqual(result["calculation"]["required_list_price_for_target_margin"], 11716.96)
        self.assertIn("test:fabric-quote", result["source_refs"])

    def test_currency_mismatch_does_not_convert_implicitly(self):
        result = calculate_apparel_margin(
            proposed_price=10000,
            price_currency="USD",
            costing=costing_input(),
        )
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["calculations"], [])
        self.assertIn("currencies differ", result["limitations"][0])

    def test_omitted_zero_cost_component_is_rejected(self):
        payload = costing_input()
        del payload["logistics_costs"]["duty"]
        with self.assertRaises(CostingInputError):
            validate_apparel_costing(payload)

    def test_unit_costs_require_explicit_source_references(self):
        payload = costing_input()
        payload["materials"][0]["source_ref"] = " "
        with self.assertRaises(CostingInputError):
            validate_apparel_costing(payload)


if __name__ == "__main__":
    unittest.main()
