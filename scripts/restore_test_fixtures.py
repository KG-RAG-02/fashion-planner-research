"""Rebuild missing synthetic fixtures offline. Preserve existing files/configs."""
from pathlib import Path
import json
from copy import deepcopy
from generate_query_coverage_fixture import generate, MARKERS
from synthetic_market_scenario import build
from synthetic_market_rag.generate import write_corpus
ROOT = Path(__file__).resolve().parents[1]

def missing(path, value):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def main():
    coverage = ROOT / 'examples/query_coverage'
    config = {'seed': 407, 'request_id': 'synthetic-query-coverage', 'generated_at_utc': '2026-11-12T00:00:00Z',
              'period_start': '2027-01', 'period_months': 36,
              'category_gender_pairs': [{'category': c, 'gender': g} for c in ['knitwear', 'footwear', 'outerwear'] for g in ['men', 'women']],
              'geographies': ['KR', 'JP'], 'channels': ['online', 'offline'], 'colors': ['black', 'white', 'navy', 'gray', 'green', 'beige'],
              'mapping': {'mapping_id': 'synthetic-query-map', 'version': '1.0'},
              'generation_assumptions': {'seasonality_by_month': [1.1, 1, .9, 1, 1.1, 1, .9, 1, 1.1, 1.2, 1.1, 1.2],
              'base_scenario_value_range_krw_million': [100, 500], 'yearly_drift': .02, 'random_noise_fraction': .03,
              'note': 'Arbitrary synthetic assumptions; not observations, forecasts or business evidence.'}}
    missing(coverage / 'config.json', config)
    if not (coverage / 'generated/manifest.json').exists(): generate(coverage / 'config.json', coverage / 'generated')
    scenario = ROOT / 'examples/synthetic_market_scenario'
    config = {'seed': 407, 'request_id': 'synthetic-market-scenario', 'generated_at_utc': '2026-11-12T00:00:00Z',
              'scope': {'geography': 'KR', 'channel': 'online', 'category': 'Outerwear', 'population': 'synthetic_panel_not_real'},
              'period_start': '2024-01', 'period_months': 24, 'mapping': {'mapping_id': 'synthetic-map', 'version': '1.0'},
              'categories': [{'category': c, 'source_category': c, 'base_monthly_value_krw_million': v} for c, v in [('Jacket', 1000), ('Coat', 2000)]],
              'sales_media': {'mobile': .7, 'desktop': .3},
              'generation': {'annual_drift': .03, 'category_noise': .04, 'media_noise': .02, 'monthly_seasonality': [1.1] * 12}}
    missing(scenario / 'scenario-config.json', config)
    if not (scenario / 'generated/manifest.json').exists(): build(json.loads((scenario / 'scenario-config.json').read_text()), scenario / 'generated')
    rag = ROOT / 'tests/fixtures/synthetic_market_rag'
    if not (rag / 'manifest.json').exists(): write_corpus(rag)
    http = ROOT / 'examples/fixtures/http_json_handoff'
    for seed in (ROOT / 'scripts/fixture_seeds/http').glob('*.json'):
        missing(http / seed.name, json.loads(seed.read_text()))
    request = json.loads((http / 'project-07-run.request.synthetic.json').read_text())
    inp = request['input'];rid = request['request_id']
    structured = json.loads((ROOT / 'scripts/fixture_seeds/women-knitwear-2028-03.request.json').read_text())
    bad = deepcopy(request);bad['contract_version'] = 'invalid'
    result = {'schema_version': '1.0', 'request_id': rid, 'agent_id': 'sales-market-agent', 'status': 'partial'}
    missing(http / 'cases.json', {**MARKERS, 'cases': [
        {'case_id': 'ok_mixed_producer_versions', 'request': request},
        {'case_id': 'result_body_lineage_preserved', 'response': {'contract_version': 'vogue-handoff/0.1', 'request_id': rid, 'producer': {'agent_id': 'sales-market-agent', 'schema_version': '1.0'}, 'status': 'partial', 'result': result}},
        {'case_id': 'transport_validation_error', 'request': bad}]})
    missing(http / 'artifact-cases.json', {**MARKERS, 'cases': [{'case_id': name, 'expected': {'erase_result': False}} for name in ['artifact_request_id_mismatch', 'artifact_hash_mismatch', 'artifact_evidence_reference_missing', 'chart_creation_failed_after_analysis']]})
    upstream = {'synthetic_fixture': True, 'request_id': rid, **deepcopy(inp)}
    upstream['trend_result']['schema_version'] = 'synthetic-trend-producer-version-A'
    upstream['trend_result']['producer_extension'] = {'raw': 'preserve-me'}
    upstream['inventory_product_result']['schema_version'] = 'synthetic-inventory-producer-version-B'
    missing(ROOT / 'tests/fixtures/upstream-envelope.synthetic.json', upstream)
    # Restore explicit source examples; the fixture scope is test data only.
    structured['query_parser']['reference_time'] = '2026-11-12T00:00:00Z'
    structured['scope'].update({'category': {'value': 'Knitwear', 'certainty': 'explicit', 'source': 'fixture'},
        'gender': {'value': 'women', 'certainty': 'explicit', 'source': 'fixture'},
        'season': None, 'metrics': ['scenario_market_value'], 'ambiguities': [],
        'period': {'start': '2028-03-01', 'end': '2028-03-31', 'certainty': 'resolved', 'rule_version': 'fixture/1', 'source': 'fixture'}})
    structured['requested_agents'] = ['sales_market']
    missing(ROOT / 'examples/synthetic_rag_analysis/women-knitwear-2028-03.request.json', structured)
    missing(rag / 'query-interpretation-cases.json', json.loads((ROOT / 'scripts/fixture_seeds/query-interpretation-cases.json').read_text()))
    print('Restored missing synthetic fixtures only; external calls=0.')
if __name__ == '__main__': main()
