"""HTTP /reviews route as configured from the environment (no injected service)."""
import email.message
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator

from market_research_agent.http_server import make_handler
from market_research_agent.reviews import REVIEW_PATH, VERSION, _REGISTRY, canonical

FIXTURES = Path(__file__).parent / 'fixtures/reviews'
TOKEN = 'test-only-token'
PROBLEM = Draft202012Validator({'$ref': 'https://contracts.vogue.invalid/workflow/0.1/common.schema.json#/$defs/problem'},
                               registry=_REGISTRY)


def fixture_request():
    return json.loads((FIXTURES / 'request.json').read_text(encoding='utf-8'))


def call(handler, raw, env, *, token=TOKEN, content_type='application/json'):
    h = object.__new__(handler)
    h.path = REVIEW_PATH
    h.headers = email.message.Message()
    h.headers['Content-Type'] = content_type
    h.headers['Content-Length'] = str(len(raw))
    if token is not None:
        h.headers['Authorization'] = 'Bearer ' + token
    h.rfile, h.wfile = BytesIO(raw), BytesIO()
    h.send_response, h.send_header, h.end_headers = Mock(), Mock(), Mock()
    with patch.dict(os.environ, {'VOGUE_AGENT_SERVICE_BEARER_TOKEN': TOKEN, **env}, clear=True):
        h.do_POST()
    return h.send_response.call_args.args[0], json.loads(h.wfile.getvalue())


class ReviewRouteTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store_dir = self.root / 'reviews'
        self.agent_factory = Mock(side_effect=AssertionError('no source work expected'))
        self.handler = make_handler(self.agent_factory)
        self.env = {'VOGUE_P07_REVIEW_ARTIFACT_DIR': str(self.store_dir)}

    def assert_problem(self, code, out, status, expected_status, retryable=False):
        self.assertEqual((status, out['error']['code']), (expected_status, code), out)
        self.assertEqual(out['contract_version'], VERSION)
        self.assertEqual((out['status'], out['error']['stage'], out['error']['retryable']),
                         ('error', 'sales_market_review', retryable))
        self.assertTrue(PROBLEM.is_valid(out), out)


class ContractVersionDispatchTests(ReviewRouteTestCase):
    def test_unsupported_versions_are_422_before_any_data_work(self):
        cases = {}
        for version in ('vogue-workflow/0.2', 'vogue-handoff/0.1', 'vogue-workflow/0.1 ', '', None, 1, ['vogue-workflow/0.1']):
            body = fixture_request()
            body['contract_version'] = version
            cases[repr(version)] = canonical(body)
        missing = fixture_request(); del missing['contract_version']
        cases['missing'] = canonical(missing)
        cases['array body'] = b'[]'
        cases['string body'] = b'"vogue-workflow/0.1"'
        for label, raw in cases.items():
            with self.subTest(version=label):
                status, out = call(self.handler, raw, self.env)
                self.assert_problem('UNSUPPORTED_CONTRACT_VERSION', out, status, 422)
        self.agent_factory.assert_not_called()
        # The artifact store was never created, so no bytes were persisted.
        self.assertFalse(self.store_dir.exists())

    def test_request_id_is_echoed_only_when_safe(self):
        body = fixture_request(); body['contract_version'] = 'vogue-workflow/9'
        status, out = call(self.handler, canonical(body), self.env)
        self.assertEqual((status, out['request_id']), (422, body['request_id']))
        body['request_id'] = 'bad id with spaces'
        status, out = call(self.handler, canonical(body), self.env)
        self.assertEqual((status, out['request_id']), (422, None))

    def test_auth_and_transport_checks_still_precede_dispatch(self):
        body = fixture_request(); body['contract_version'] = 'vogue-workflow/9'
        status, out = call(self.handler, canonical(body), self.env, token=None)
        self.assert_problem('UNAUTHORIZED', out, status, 401)
        status, out = call(self.handler, canonical(body), self.env, content_type='text/plain')
        self.assert_problem('UNSUPPORTED_MEDIA_TYPE', out, status, 415)
        status, out = call(self.handler, b'{"contract_version": 1,', self.env)
        self.assert_problem('INVALID_JSON', out, status, 400)
        self.assertFalse(self.store_dir.exists())


if __name__ == '__main__':
    unittest.main()
