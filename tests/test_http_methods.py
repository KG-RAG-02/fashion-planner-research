"""Wrong methods on the v1 agent routes return 405 with Allow (conventions section 6).

A live stdlib server on an ephemeral loopback port exercises the real method
dispatch (unknown do_<METHOD> handlers used to fall through to 404/501).
"""
import email.message
from http.server import ThreadingHTTPServer
import http.client
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator

from market_research_agent.http_server import make_handler, serve
from market_research_agent.reviews import REVIEW_PATH, _REGISTRY

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = Draft202012Validator({'$ref': 'https://contracts.vogue.invalid/workflow/0.1/common.schema.json#/$defs/problem'},
                               registry=_REGISTRY)
HANDOFF_ERROR = Draft202012Validator(json.loads(
    (ROOT / 'contracts/schemas/vogue-handoff-0.1-error.schema.json').read_text(encoding='utf-8')))
LEGACY_ROUTES = ('/v1/agents/sales-market-agent/runs', '/v1/agents/market-research-agent/runs',
                 '/v1/agents/sales-market-agent/structured-runs')
WRONG_METHODS = ('GET', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'TRACE')


class LiveServerTestCase(unittest.TestCase):
    handler_kwargs: dict = {}

    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(**self.handler_kwargs))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.getheader('Allow'), response.read()
        finally:
            connection.close()

    def raw_exchange(self, request_line):
        """Send one request on a raw socket and return every byte until close."""
        with socket.create_connection(self.server.server_address, timeout=10) as client:
            client.sendall(request_line.encode('ascii') + b'\r\nHost: test\r\nConnection: close\r\n\r\n')
            chunks = []
            while chunk := client.recv(65536):
                chunks.append(chunk)
        return b''.join(chunks)


class WrongMethodTests(LiveServerTestCase):
    def test_review_route_answers_405_with_allow_and_a_workflow_problem(self):
        for method in WRONG_METHODS:
            with self.subTest(method=method):
                status, allow, body = self.request(method, REVIEW_PATH)
                self.assertEqual((status, allow), (405, 'POST'))
                problem = json.loads(body)
                self.assertTrue(PROBLEM.is_valid(problem), problem)
                self.assertEqual((problem['error']['code'], problem['error']['retryable'], problem['request_id']),
                                 ('METHOD_NOT_ALLOWED', False, None))

    def test_legacy_routes_answer_405_with_allow_and_their_handoff_error_envelope(self):
        for path in LEGACY_ROUTES:
            for method in WRONG_METHODS:
                with self.subTest(path=path, method=method):
                    status, allow, body = self.request(method, path)
                    self.assertEqual((status, allow), (405, 'POST'))
                    error = json.loads(body)
                    self.assertTrue(HANDOFF_ERROR.is_valid(error), error)
                    self.assertEqual(error['error']['code'], 'METHOD_NOT_ALLOWED')

    def test_head_answers_405_and_allow_without_a_body(self):
        for path in (REVIEW_PATH, *LEGACY_ROUTES):
            with self.subTest(path=path):
                raw = self.raw_exchange(f'HEAD {path} HTTP/1.1')
                head, _, rest = raw.partition(b'\r\n\r\n')
                self.assertTrue(head.startswith(b'HTTP/1.0 405') or head.startswith(b'HTTP/1.1 405'), head)
                self.assertIn(b'\r\nAllow: POST', head)
                self.assertEqual(rest, b'')

    def test_query_string_and_missing_auth_do_not_change_the_405(self):
        status, allow, _ = self.request('GET', REVIEW_PATH + '?probe=1')
        self.assertEqual((status, allow), (405, 'POST'))
        status, allow, _ = self.request('DELETE', '/v1/agents/sales-market-agent/runs?x=1')
        self.assertEqual((status, allow), (405, 'POST'))

    def test_other_paths_and_post_keep_their_behaviour(self):
        status, allow, body = self.request('GET', '/v1/agents/other-agent/runs')
        self.assertEqual((status, allow, json.loads(body)['error']['code']), (404, None, 'NOT_FOUND'))
        status, allow, _ = self.request('GET', '/v1/agents/market-research-agent/structured-runs')
        self.assertEqual((status, allow), (404, None))
        status, _, _ = self.request('PUT', '/api/health')
        self.assertEqual(status, 501)
        status, _, _ = self.request('GET', '/api/health')
        self.assertEqual(status, 200)
        with patch.dict(os.environ, {'VOGUE_AGENT_SERVICE_BEARER_TOKEN': 'test-only-token'}):
            for path in (REVIEW_PATH, *LEGACY_ROUTES):
                with self.subTest(path=path):
                    status, allow, _ = self.request('POST', path, body=b'{}',
                                                    headers={'Content-Type': 'application/json'})
                    self.assertEqual((status, allow), (401, None))


def in_process_health(handler):
    h = object.__new__(handler)
    h.path, h.headers, h.wfile = '/api/health', email.message.Message(), BytesIO()
    h.send_response, h.send_header, h.end_headers = Mock(), Mock(), Mock()
    h.do_GET()
    return h.send_response.call_args.args[0], json.loads(h.wfile.getvalue())


class LocalHealthModeTests(LiveServerTestCase):
    def test_local_development_mode_without_auth(self):
        status, _, body = self.request('GET', '/api/health')
        self.assertEqual((status, json.loads(body)), (200, {'status': 'ok', 'mode': 'local-development'}))


class ServiceHealthModeTests(LiveServerTestCase):
    handler_kwargs = {'service_mode': True}

    def test_service_mode_is_reported_without_auth(self):
        status, _, body = self.request('GET', '/api/health')  # no Authorization header
        self.assertEqual((status, json.loads(body)), (200, {'status': 'ok', 'mode': 'service'}))

    def test_serve_passes_the_real_mode_to_the_handler(self):
        for kwargs, env, expected in (
                ({'host': '0.0.0.0', 'service_mode': True}, {'VOGUE_AGENT_SERVICE_BEARER_TOKEN': 'test-only'}, 'service'),
                ({'host': '127.0.0.1'}, {}, 'local-development')):
            with self.subTest(expected=expected), patch.dict(os.environ, env, clear=True), \
                    patch('market_research_agent.http_server.ThreadingHTTPServer') as server_type, \
                    patch('builtins.print'):
                serve(port=0, **kwargs)
                status, health = in_process_health(server_type.call_args.args[1])
                self.assertEqual((status, health), (200, {'status': 'ok', 'mode': expected}))

    def test_synthetic_simulation_keeps_its_labels(self):
        status, health = in_process_health(make_handler(service_mode=True, synthetic_simulation_enabled=True))
        self.assertEqual((status, health['mode'], health['synthetic'], health['data_nature'], health['operational_eligible']),
                         (200, 'service', True, 'synthetic_test', False))
        status, health = in_process_health(make_handler(synthetic_simulation_enabled=True))
        self.assertEqual((status, health['mode'], health['synthetic']), (200, 'synthetic_simulation', True))


if __name__ == '__main__':
    unittest.main()
