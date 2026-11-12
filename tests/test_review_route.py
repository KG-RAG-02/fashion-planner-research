"""HTTP /reviews route as configured from the environment (no injected service)."""
from copy import deepcopy
import email.message
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator

from market_research_agent.artifact_reader import (MAX_ARTIFACT_BYTES, DirectoryArtifacts,
                                                   artifact_reader_from_env, require_artifact_reader)
from market_research_agent.domain.models import (EvidenceRecord, FindingKind, PermissionStatus,
                                                 ResearchFinding, ResultStatus, SourceReport)
from market_research_agent.domain.sales import SalesDataResult, SalesObservation
from market_research_agent.http_server import make_handler, review_agent_factory, serve
from market_research_agent.reviews import (REVIEW_PATH, VERSION, _REGISTRY, ReviewError, canonical,
                                           fingerprint, parse_json, parse_review_response, review_artifact_dir)
from market_research_agent.sources.market.synthetic_review_fixture import (SyntheticReviewFixtureSource,
                                                                           default_bundle_path, load_bundle)
from market_research_agent.sources.shared.r2_object_store import R2ObjectStore
from market_research_agent.workflows.market_research import MarketResearchAgent

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures/reviews'
TOKEN = 'test-only-token'
PROBLEM = Draft202012Validator({'$ref': 'https://contracts.vogue.invalid/workflow/0.1/common.schema.json#/$defs/problem'},
                               registry=_REGISTRY)


def fixture_request():
    return json.loads((FIXTURES / 'request.json').read_text(encoding='utf-8'))


def raw_bytes(document):
    """A non-canonical formatting, like real producer bytes."""
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')


def mint_p08(request, root):
    """Re-point every retained raw_artifact to P08 content-addressed blobs under root.

    Mirrors P08: bytes at <store>/blobs/<sha256>, URI artifact://p08/blobs/<sha256>,
    mounted for P07 as <root>/p08/blobs. Dependent hashes are recomputed.
    """
    request = deepcopy(request)
    blobs = root / 'p08' / 'blobs'
    blobs.mkdir(parents=True, exist_ok=True)

    def mint(node):
        data = raw_bytes(node['raw_response'])
        digest = sha256(data).hexdigest()
        (blobs / digest).write_bytes(data)
        node['raw_artifact'] = {'artifact_id': 'p08:' + digest, 'uri': 'artifact://p08/blobs/' + digest,
                                'sha256': digest, 'media_type': 'application/json',
                                'size_bytes': len(data), 'immutable': True}
    draft = request['input']['planning_draft']
    result = draft['raw_response']['result']
    for key in ('trend_response', 'stock_response'):
        mint(result['source_request']['input'][key])
    result['source_request_sha256'] = fingerprint(result['source_request'])
    draft['content_sha256'] = fingerprint(draft['raw_response'])
    mint(draft)
    return request


def call(handler, raw, env, **kwargs):
    status, body = call_raw(handler, raw, env, **kwargs)
    return status, json.loads(body)


def call_raw(handler, raw, env, *, token=TOKEN, content_type='application/json'):
    """Return the status and the exact response bytes written by the handler."""
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
    return h.send_response.call_args.args[0], h.wfile.getvalue()


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


class ArtifactReaderRouteTests(ReviewRouteTestCase):
    """VOGUE_UPSTREAM_ARTIFACT_ROOT reader injected into the HTTP /reviews path."""

    def setUp(self):
        super().setUp()
        self.artifact_root = self.root / 'artifacts'
        self.request = mint_p08(fixture_request(), self.artifact_root)
        self.reader_env = {**self.env, 'VOGUE_UPSTREAM_ARTIFACT_ROOT': str(self.artifact_root)}
        self.draft_ref = self.request['input']['planning_draft']['raw_artifact']
        self.draft_blob = self.artifact_root / 'p08/blobs' / self.draft_ref['sha256']

    def post(self, request=None, env=None):
        return call(self.handler, canonical(self.request if request is None else request),
                    self.reader_env if env is None else env)

    def assert_rejected(self, code, expected_status, retryable=False, **kwargs):
        status, out = self.post(**kwargs)
        self.assert_problem(code, out, status, expected_status, retryable)
        self.agent_factory.assert_not_called()
        persisted = sorted(self.store_dir.iterdir()) if self.store_dir.exists() else []
        self.assertEqual(persisted, [], 'nothing may be persisted before artifacts verify')

    def with_draft_uri(self, uri):
        request = deepcopy(self.request)
        request['input']['planning_draft']['raw_artifact']['uri'] = uri
        return request

    def test_p08_blob_bytes_are_verified_on_the_happy_path(self):
        agent = Mock(return_value=MarketResearchAgent())
        status, out = call(make_handler(agent), canonical(self.request), self.reader_env)
        self.assertEqual((status, out['status']), (200, 'no_results'), out)
        agent.assert_called_once()
        self.assertTrue(all(node['raw_artifact']['uri'].startswith('artifact://p08/blobs/')
                            for node in (self.request['input']['planning_draft'],)))
        limitations = out['result']['limitations']
        self.assertIn('Incoming raw_artifact bytes (3 retained responses) were read from the read-only upstream '
                      'artifact mount and verified by size, SHA-256 and parsed JSON before review.', limitations)
        self.assertFalse(any('were not fetched' in item for item in limitations))
        self.assertEqual(parse_review_response(out, self.request), out)

    def test_unset_or_blank_root_fails_closed_with_503(self):
        self.assert_rejected('ARTIFACT_READER_REQUIRED', 503, retryable=True, env=self.env)
        self.assert_rejected('ARTIFACT_READER_REQUIRED', 503, retryable=True,
                             env={**self.env, 'VOGUE_UPSTREAM_ARTIFACT_ROOT': '  '})

    def test_schema_violation_is_reported_before_the_missing_reader(self):
        request = deepcopy(self.request)
        request['input']['unexpected'] = True
        self.assert_rejected('INVALID_CONTRACT', 422, request=request, env=self.env)

    def test_size_and_hash_mismatches_are_422(self):
        good = self.draft_blob.read_bytes()
        self.draft_blob.write_bytes(good[:-1])
        self.assert_rejected('ARTIFACT_SIZE_MISMATCH', 422)
        self.draft_blob.write_bytes(good[:-1] + b' ')
        self.assert_rejected('ARTIFACT_HASH_MISMATCH', 422)
        self.draft_blob.write_bytes(good)
        nested = self.request['input']['planning_draft']['raw_response']['result']['source_request']['input']
        trend_blob = self.artifact_root / 'p08/blobs' / nested['trend_response']['raw_artifact']['sha256']
        trend_blob.write_bytes(trend_blob.read_bytes()[:-1] + b' ')
        self.assert_rejected('ARTIFACT_HASH_MISMATCH', 422)

    def test_verified_bytes_must_parse_to_the_retained_response(self):
        request = deepcopy(self.request)
        nested = request['input']['planning_draft']['raw_response']['result']['source_request']['input']
        request['input']['planning_draft']['raw_artifact'] = dict(nested['trend_response']['raw_artifact'])
        self.assert_rejected('RAW_ARTIFACT_OBJECT_MISMATCH', 422, request=request)

    def test_traversal_and_empty_segments_are_422(self):
        digest = self.draft_ref['sha256']
        for uri in ('artifact://p08/../p08/blobs/' + digest, 'artifact://p08/./blobs/' + digest,
                    'artifact://p08//blobs/' + digest, 'artifact:///p08/blobs/' + digest,
                    'artifact://../artifacts/p08/blobs/' + digest, 'artifact://p08/blobs/' + digest + '/..'):
            with self.subTest(uri=uri):
                self.assert_rejected('INVALID_ARTIFACT_PATH', 422, request=self.with_draft_uri(uri))

    def test_symlinks_are_refused_even_inside_the_root(self):
        moved = self.artifact_root / 'p08' / 'draft-bytes'
        self.draft_blob.rename(moved)
        self.draft_blob.symlink_to(moved)
        self.assert_rejected('INVALID_ARTIFACT_PATH', 422)
        self.draft_blob.unlink()
        moved.rename(self.draft_blob)
        blobs = self.artifact_root / 'p08' / 'blobs'
        blobs.rename(self.artifact_root / 'p08' / 'real-blobs')
        blobs.symlink_to(self.artifact_root / 'p08' / 'real-blobs', target_is_directory=True)
        self.assert_rejected('INVALID_ARTIFACT_PATH', 422)

    def test_missing_bytes_or_missing_root_are_503(self):
        self.assert_rejected('ARTIFACT_UNAVAILABLE', 503, retryable=True,
                             env={**self.env, 'VOGUE_UPSTREAM_ARTIFACT_ROOT': str(self.root / 'absent')})
        self.draft_blob.unlink()
        self.assert_rejected('ARTIFACT_UNAVAILABLE', 503, retryable=True)


def with_draft_scope(request, *, category, season, title):
    """Same fixture Draft with another (possibly null) category/season; hashes recomputed."""
    request = deepcopy(request)
    result = request['input']['planning_draft']['raw_response']['result']
    result['draft'].update(category=category, season=season, title=title)
    result['draft_identity']['content_sha256'] = fingerprint(result['draft'])
    return request


def refuse_network(*args, **kwargs):
    raise AssertionError('network access attempted')


class SyntheticReviewSourceTests(ReviewRouteTestCase):
    """VOGUE_P07_REVIEW_SOURCE=synthetic_local_fixture: offline, deterministic, labelled synthetic."""

    def setUp(self):
        super().setUp()
        self.artifact_root = self.root / 'artifacts'
        self.env = {**self.env, 'VOGUE_UPSTREAM_ARTIFACT_ROOT': str(self.artifact_root),
                    'VOGUE_P07_REVIEW_SOURCE': 'synthetic_local_fixture'}

    def review(self, request, store):
        env = {**self.env, 'VOGUE_P07_REVIEW_ARTIFACT_DIR': str(self.root / store)}
        with patch('socket.socket.connect', refuse_network), patch('socket.create_connection', refuse_network), \
                patch.object(R2ObjectStore, 'from_env', side_effect=AssertionError('R2 is not allowed')):
            return call(self.handler, canonical(mint_p08(request, self.artifact_root)), env)

    def test_valid_draft_yields_partial_review_with_synthetic_metrics(self):
        request = mint_p08(fixture_request(), self.artifact_root)
        status, out = self.review(request, 'store-a')
        self.assertEqual((status, out['status'], out['result']['status']), (200, 'partial', 'partial'), out)
        self.agent_factory.assert_not_called()  # the configured default sources are bypassed
        review = out['result']['review']
        valued = [m for m in review['metrics'] if m['value'] is not None]
        self.assertEqual(sorted((m['name'], m['value'], m['period']) for m in valued), [
            ('market_data.scenario_market_value', 223.98, '2027-05-01/2027-05-31'),
            ('market_data.scenario_market_value', 280.43, '2027-03-01/2027-03-31'),
            ('market_data.scenario_market_value', 493.28, '2027-04-01/2027-04-30'),
            ('sales_data.scenario_units_sold', 428, '2027-04-01/2027-04-30'),
            ('sales_data.scenario_units_sold', 2057, '2027-05-01/2027-05-31'),
            ('sales_data.scenario_units_sold', 2450, '2027-03-01/2027-03-31'),
        ])
        for metric in valued:
            self.assertEqual((metric['observation']['synthetic'], metric['observation']['data_nature']),
                             (True, 'synthetic_fixture'))
            self.assertTrue(metric['evidence_refs'])
        self.assertEqual((review['observation']['synthetic'], review['observation']['data_nature']),
                         (True, 'synthetic_fixture'))
        current = next(m for m in review['metrics'] if m['name'] == 'current_company_sales')
        self.assertIsNone(current['value'])
        self.assertTrue(current['missing_reason'])
        limitations = out['result']['limitations']
        self.assertTrue(any(item.startswith('SYNTHETIC TEST DATA ONLY') for item in limitations))
        self.assertTrue(any('not filtered by' in item for item in limitations))
        self.assertEqual(review['findings'], [])
        self.assertEqual(parse_review_response(out, request), out)

    def test_any_valid_draft_gets_the_same_values(self):
        def values(out):
            return sorted((m['name'], m['value'], m['period']) for m in out['result']['review']['metrics']
                          if m['value'] is not None)
        first = self.review(fixture_request(), 'store-1')
        repeat = self.review(fixture_request(), 'store-2')
        null_scope = self.review(with_draft_scope(fixture_request(), category=None, season=None,
                                                  title='시즌 미정 카테고리 미정 기획 초안'), 'store-3')
        other = self.review(with_draft_scope(fixture_request(), category='아우터', season='2028FW',
                                             title='2028FW 아우터 기획 초안'), 'store-4')
        for status, out in (first, repeat, null_scope, other):
            self.assertEqual((status, out['status']), (200, 'partial'), out)
        self.assertEqual(values(first[1]), values(repeat[1]))
        self.assertEqual(values(first[1]), values(null_scope[1]))
        self.assertEqual(values(first[1]), values(other[1]))

    def test_unset_keeps_the_configured_default_and_unknown_values_fail_closed(self):
        agent = Mock(return_value=MarketResearchAgent())
        request = canonical(mint_p08(fixture_request(), self.artifact_root))
        for value in ('', '  '):
            status, out = call(make_handler(agent), request, {**self.env, 'VOGUE_P07_REVIEW_SOURCE': value})
            self.assertEqual((status, out['status']), (200, 'no_results'), out)
        self.assertEqual(agent.call_count, 1)  # second call replays the stored response
        status, out = call(self.handler, request, {**self.env, 'VOGUE_P07_REVIEW_SOURCE': 'r2'})
        self.assert_problem('REVIEW_SOURCE_UNSUPPORTED', out, status, 503, retryable=True)
        with patch.dict(os.environ, {'VOGUE_AGENT_SERVICE_BEARER_TOKEN': TOKEN,
                                     'VOGUE_P07_REVIEW_SOURCE': 'bogus'}, clear=True), \
                patch('market_research_agent.http_server.ThreadingHTTPServer') as server:
            with self.assertRaises(ReviewError):
                serve(host='127.0.0.1', port=0)
            server.assert_not_called()
        self.assertIs(review_agent_factory(agent, {}), agent)

    def test_invalid_bundle_is_a_source_error_not_values(self):
        bundle = json.loads(default_bundle_path().read_text(encoding='utf-8'))
        broken = self.root / 'bundle.json'
        for mutate in (lambda b: b.update(synthetic=False), lambda b: b['sales_like'][0].update(value=None),
                       lambda b: b['market_like'][0].update(evidence_ids=['missing']),
                       lambda b: b['evidence'][0].update(permission_status='authorized')):
            candidate = deepcopy(bundle); mutate(candidate)
            broken.write_text(json.dumps(candidate), encoding='utf-8')
            report = SyntheticReviewFixtureSource(broken).research(None)
            self.assertEqual(report.status.value, 'error')
            self.assertEqual((report.sales_data.observations, report.market_data.observations), ((), ()))
        self.assertEqual(load_bundle()['generator']['seed'], 407)

    def test_packaged_bundle_is_reproducible_from_the_seed_407_generator(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/build_synthetic_review_fixture.py'), '--check'],
                                cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class NonEmptyReviewRoundTripTests(ReviewRouteTestCase):
    """Regression P07-QA-SOURCE: a non-empty Review whose evidence carries the
    p07_source_snapshot artifact is accepted by P07's own parser round-trip."""

    def setUp(self):
        super().setUp()
        self.artifact_root = self.root / 'artifacts'
        self.peer_root = self.root / 'peer'  # P08 mounts P07 reviews at <peer>/p07/reviews
        self.request = mint_p08(fixture_request(), self.artifact_root)
        self.env = {'VOGUE_P07_REVIEW_ARTIFACT_DIR': str(self.peer_root / 'p07/reviews'),
                    'VOGUE_UPSTREAM_ARTIFACT_ROOT': str(self.artifact_root)}
        source = Mock()
        source.research.return_value = SourceReport(
            status=ResultStatus.OK,
            findings=(ResearchFinding('F-QA-1', 'sales', 'Historical purchase rows were observed for the test scope.',
                                      kind=FindingKind.FACT, evidence_ids=('E-QA-1',)),),
            evidence=(EvidenceRecord('E-QA-1', 'hm-historical', 'historical_sales', 'H&M test historical rows',
                                     permission_status=PermissionStatus.AUTHORIZED, as_of='2020-09-22'),),
            sales_data=SalesDataResult('ok', observations=(SalesObservation(
                'O-QA-1', 'purchase_rows', 3, 'purchase_rows', ('E-QA-1',), period_start='2020-09-21',
                period_end='2020-09-27', as_of='2020-09-27', data_nature='historical_external'),)))
        self.handler = make_handler(Mock(side_effect=lambda: MarketResearchAgent(source)))

    def produce(self):
        status, body = call_raw(self.handler, canonical(self.request), self.env)
        self.assertEqual(status, 200, body)
        return body

    def test_wire_bytes_round_trip_through_the_strict_parser(self):
        body = self.produce()
        document = parse_json(body)  # strict: unique keys, finite numbers, UTF-8
        self.assertEqual(document['status'], 'partial')
        review = document['result']['review']
        self.assertTrue(review['findings'] and all(f['evidence_refs'] for f in review['findings']))
        self.assertTrue(any(m['value'] is not None for m in review['metrics']))
        snapshot = next(e for e in review['evidence'] if e.get('record_type') == 'p07_source_snapshot')
        self.assertIn('artifact', snapshot)
        self.assertEqual(parse_review_response(document, self.request), document)
        # The replayed response is accepted too.
        self.assertEqual(parse_review_response(parse_json(self.produce()), self.request), document)

    def test_source_and_review_artifacts_resolve_through_the_peer_mount(self):
        document = parse_json(self.produce())
        review = document['result']['review']
        snapshot = next(e for e in review['evidence'] if e.get('record_type') == 'p07_source_snapshot')
        peer = DirectoryArtifacts(self.peer_root)
        source_bytes = peer(snapshot['artifact'])
        self.assertEqual(parse_json(source_bytes), snapshot['source_data'])
        self.assertEqual(fingerprint(snapshot['source_data']), snapshot['source_response_sha256'])
        self.assertIn(snapshot['artifact'], document['result']['artifacts'])
        for artifact in document['result']['artifacts']:
            self.assertTrue(artifact['uri'].startswith('artifact://p07/reviews/'))
            self.assertEqual(sha256(peer(artifact)).hexdigest(), artifact['sha256'])

    def test_tampered_non_empty_reviews_are_rejected(self):
        document = parse_json(self.produce())

        def evidence(doc):
            return doc['result']['review']['evidence']

        def rehash(doc):
            doc['result']['review_identity']['content_sha256'] = fingerprint(doc['result']['review'])
            return doc
        changed_data = deepcopy(document)
        evidence(changed_data)[-1]['source_data']['status'] = 'ok-but-edited'
        changed_draft = deepcopy(document)
        evidence(changed_draft)[-1]['retained_planning_draft']['observation']['source'] = 'edited'
        dropped = deepcopy(document)
        evidence(dropped).pop()
        for label, candidate, code in (('source_data', changed_data, 'SOURCE_HASH_MISMATCH'),
                                       ('retained draft', changed_draft, 'RETAINED_DRAFT_CHANGED'),
                                       ('snapshot dropped', dropped, 'EVIDENCE_DOCUMENT_MISSING')):
            with self.subTest(label), self.assertRaises(ReviewError) as caught:
                parse_review_response(rehash(candidate), self.request)
            self.assertEqual(caught.exception.code, code)


class ReviewArtifactDirTests(ReviewRouteTestCase):
    def test_env_selects_the_store_and_blank_keeps_the_default(self):
        self.assertEqual(review_artifact_dir({}), Path('.local/review-artifacts'))
        self.assertEqual(review_artifact_dir({'VOGUE_P07_REVIEW_ARTIFACT_DIR': ' '}), Path('.local/review-artifacts'))
        self.assertEqual(review_artifact_dir({'VOGUE_P07_REVIEW_ARTIFACT_DIR': '/data/reviews'}), Path('/data/reviews'))

    def test_review_artifacts_land_in_the_configured_directory(self):
        artifact_root = self.root / 'artifacts'
        request = mint_p08(fixture_request(), artifact_root)
        env = {**self.env, 'VOGUE_UPSTREAM_ARTIFACT_ROOT': str(artifact_root)}
        status, out = call(make_handler(Mock(return_value=MarketResearchAgent())), canonical(request), env)
        self.assertEqual(status, 200, out)
        artifacts = out['result']['artifacts']
        self.assertTrue(artifacts)
        for artifact in artifacts:
            self.assertRegex(artifact['uri'], r'^artifact://p07/reviews/[0-9a-f]{64}\.json$')
            path = self.store_dir / artifact['uri'].rsplit('/', 1)[1]
            self.assertEqual(sha256(path.read_bytes()).hexdigest(), artifact['sha256'])
        self.assertTrue((self.store_dir / 'reviews.sqlite3').is_file())


class DirectoryArtifactsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / 'root'
        (self.root / 'ns').mkdir(parents=True)
        self.data = b'{"a": 1}\n'
        (self.root / 'ns' / 'item').write_bytes(self.data)
        self.ref = {'uri': 'artifact://ns/item', 'size_bytes': len(self.data), 'sha256': sha256(self.data).hexdigest()}
        self.reader = DirectoryArtifacts(self.root)

    def assert_code(self, code, ref, status=422):
        with self.assertRaises(ReviewError) as caught:
            self.reader(ref)
        self.assertEqual((caught.exception.code, caught.exception.http_status), (code, status))

    def test_reads_exact_bytes(self):
        self.assertEqual(self.reader(self.ref), self.data)

    def test_malformed_references(self):
        for uri in ('file:///etc/passwd', 'artifact://ns/it em', 'artifact://', 'ARTIFACT://ns/item', 7):
            with self.subTest(uri=uri):
                self.assert_code('INVALID_ARTIFACT_URI', {**self.ref, 'uri': uri})
        for key, value in (('size_bytes', True), ('size_bytes', 0), ('size_bytes', '9'), ('sha256', 'A' * 64)):
            with self.subTest(key=key, value=value):
                self.assert_code('INVALID_ARTIFACT_URI', {**self.ref, key: value})
        self.assert_code('INVALID_ARTIFACT_URI', 'artifact://ns/item')
        self.assert_code('ARTIFACT_TOO_LARGE', {**self.ref, 'size_bytes': MAX_ARTIFACT_BYTES + 1}, 413)

    def test_escaping_symlink_and_non_file_targets(self):
        (self.base / 'outside').write_bytes(self.data)
        (self.root / 'ns' / 'escape').symlink_to(self.base / 'outside')
        self.assert_code('INVALID_ARTIFACT_PATH', {**self.ref, 'uri': 'artifact://ns/escape'})
        (self.root / 'ns' / 'dir').mkdir()
        self.assert_code('ARTIFACT_UNAVAILABLE', {**self.ref, 'uri': 'artifact://ns/dir'}, 503)
        self.assert_code('ARTIFACT_UNAVAILABLE', {**self.ref, 'uri': 'artifact://ns/item/child'}, 503)

    def test_size_and_digest_are_checked_against_the_file(self):
        self.assert_code('ARTIFACT_SIZE_MISMATCH', {**self.ref, 'size_bytes': len(self.data) + 1})
        self.assert_code('ARTIFACT_HASH_MISMATCH', {**self.ref, 'sha256': '0' * 64})

    def test_reader_selection_from_environment(self):
        self.assertIs(artifact_reader_from_env({}), require_artifact_reader)
        self.assertIs(artifact_reader_from_env({'VOGUE_UPSTREAM_ARTIFACT_ROOT': ' '}), require_artifact_reader)
        reader = artifact_reader_from_env({'VOGUE_UPSTREAM_ARTIFACT_ROOT': str(self.root)})
        self.assertIsInstance(reader, DirectoryArtifacts)
        self.assertEqual(reader(self.ref), self.data)
        with self.assertRaises(ReviewError) as caught:
            require_artifact_reader(self.ref)
        self.assertEqual((caught.exception.code, caught.exception.http_status), ('ARTIFACT_READER_REQUIRED', 503))


if __name__ == '__main__':
    unittest.main()
