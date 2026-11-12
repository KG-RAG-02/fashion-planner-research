"""Independent P07 parser/serializer and authenticated route regressions."""
import copy
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from market_research_agent.reviews import *
from market_research_agent.http_server import make_handler, serve
from market_research_agent.domain.models import SourceReport, ResultStatus, EvidenceRecord, PermissionStatus
from market_research_agent.domain.sales import SalesDataResult, SalesObservation
from market_research_agent.workflows.market_research import MarketResearchAgent

FIXTURES = Path(__file__).parent / 'fixtures/reviews'


def request(): return json.loads((FIXTURES / 'request.json').read_text())


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = ReviewArtifactStore(self.temp.name)
        self.agent = Mock(return_value=MarketResearchAgent())
        self.service = SalesMarketReviewService(self.agent, self.store)

    def post(self, body, token='test-only', **headers):
        raw = canonical(body) if isinstance(body, dict) else body
        handler = make_handler(review_service=self.service)
        h = object.__new__(handler)
        h.path = REVIEW_PATH;h.headers = __import__('email.message', fromlist=['Message']).Message()
        for k,v in {'Content-Type':'application/json','Content-Length':str(len(raw)), **headers}.items(): h.headers[k]=v
        if token is not None: h.headers['Authorization']='Bearer '+token
        h.rfile=BytesIO(raw);h.wfile=BytesIO();h.send_response=Mock();h.send_header=Mock();h.end_headers=Mock()
        with patch.dict(os.environ, {'VOGUE_AGENT_SERVICE_BEARER_TOKEN':'test-only'}, clear=True): h.do_POST()
        return h.send_response.call_args.args[0], json.loads(h.wfile.getvalue())

    def test_no_results_is_not_normalized_or_zero(self):
        code, out = self.post(request())
        self.assertEqual((code,out['status']), (200,'no_results'))
        self.assertTrue(all(m['value'] is None and m['missing_reason'] for m in out['result']['review']['metrics']))
        parse_review_response(out, request())

    def test_raw_request_bytes_and_full_draft_preserved(self):
        req=request();raw=json.dumps(req,indent=3,ensure_ascii=False).encode()
        code,out=self.post(raw);self.assertEqual(code,200)
        a=out['result']['artifacts'][0]
        self.assertEqual((Path(self.temp.name)/(a['sha256']+'.json')).read_bytes(),raw)
        retained=out['result']['review']['evidence'][-1]['retained_planning_draft']
        self.assertEqual(retained,req['input']['planning_draft'])
        self.assertEqual(fingerprint(retained['raw_response']),retained['content_sha256'])

    def test_detached_parser_and_derived_view(self):
        original=request();snap,view=parse_review_request(original)
        snap['input']['planning_draft']['raw_response']['result']['draft']['title']='changed'
        self.assertNotEqual(snap,original)
        self.assertEqual(view['purpose'],'search_only')
        self.assertEqual(view['product']['season'],'2027SS')

    def test_no_source_call_for_missing_wrong_token(self):
        for token in [None,'wrong','잘못된토큰']:
            code,out=self.post(request(),token);self.assertEqual(code,401)
        self.agent.assert_not_called()

    def test_valid_token_malformed_json(self):
        for raw in [b'{bad',b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":1e999}',b'\xff']:
            with self.subTest(raw=raw): self.assertEqual(self.post(raw)[0],400)
        self.agent.assert_not_called()

    def test_media_and_size(self):
        self.assertEqual(self.post(b'{}',**{'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.post(b'{}',**{'Content-Length':str(MAX_BODY_BYTES+1)})[0],413)
        self.assertEqual(self.post(b'{}',**{'Transfer-Encoding':'chunked'})[0],400)

    def test_bad_id_producer_version_hash_revision(self):
        for path,value in [
            (['request_id'],'wrong'),(['contract_version'],'bad'),
            (['input','planning_draft','raw_response','producer','agent_id'],'wrong'),
            (['input','planning_draft','raw_response','result','schema_version'],'bad'),
            (['input','planning_draft','raw_response','result','draft_identity','revision'],0),
            (['input','planning_draft','raw_response','result','draft_identity','content_sha256'],'0'*64),
            (['input','planning_draft','content_sha256'],'0'*64)]:
            r=request(); node=r
            for k in path[:-1]:node=node[k]
            node[path[-1]]=value
            with self.subTest(path=path):self.assertEqual(self.post(r)[0],422)
        self.agent.assert_not_called()

    def test_hash_consistent_but_wrong_draft_hash_rejected(self):
        r=request();d=r['input']['planning_draft'];d['raw_response']['result']['draft_identity']['content_sha256']='0'*64
        d['content_sha256']=fingerprint(d['raw_response'])
        self.assertEqual(self.post(r)[0],422)

    def test_derived_substitution_and_upstream_error_rejected(self):
        r=request();_,v=parse_review_request(r);r['input']['derived_view']=v;v['product']['category']='fake'
        self.assertEqual(self.post(r)[0],422)
        r=request();d=r['input']['planning_draft'];d['raw_response']['status']='error';d['content_sha256']=fingerprint(d['raw_response'])
        self.assertEqual(self.post(r)[0],422)

    def test_timeout_returns_error_without_secret(self):
        source=Mock();source.research.side_effect=TimeoutError('private token')
        self.agent.return_value=MarketResearchAgent(source)
        code,out=self.post(request())
        self.assertEqual(code,504);self.assertEqual(out['status'],'error')
        self.assertNotIn('private token',json.dumps(out))
        self.assertTrue(out['result']['errors'])

    def test_historical_metrics_keep_unit_period_and_details(self):
        source=Mock(); source.research.return_value=SourceReport(
            status=ResultStatus.OK,
            evidence=(EvidenceRecord('E1','hm-historical','historical','H&M test historical',
                      permission_status=PermissionStatus.AUTHORIZED, as_of='2020-09-22'),),
            sales_data=SalesDataResult('ok', observations=(SalesObservation('o1','purchase_rows',0,'rows',('E1',),
                       period_start='2020-09-01',period_end='2020-09-22',as_of='2020-09-22',data_nature='historical_external'),)))
        self.agent.return_value=MarketResearchAgent(source)
        code,out=self.post(request());self.assertEqual(code,200)
        m=out['result']['review']['metrics'][0]
        self.assertEqual(m['value'],0);self.assertEqual(m['unit'],'rows')
        self.assertEqual(m['observation']['data_nature'],'historical_external')
        self.assertEqual(m['comparability'],'unknown')
        detail=out['result']['review']['evidence'][-1]['source_data']
        self.assertIn('sales_data',detail);self.assertIn('comparability',detail)

    def test_coordinator_review_roundtrip_and_wrong_revision(self):
        out=json.loads((FIXTURES/'coordinator-response.json').read_text())
        self.assertEqual(parse_review_response(out,request()),out)
        out['result']['review']['reviewed_draft']['revision']+=1
        out['result']['review_identity']['content_sha256']=fingerprint(out['result']['review'])
        with self.assertRaises(ReviewError):parse_review_response(out,request())

    def test_persistent_replay_and_revision_conflict(self):
        r = request(); first = self.service.review(r)
        second = SalesMarketReviewService(self.agent, self.store).review(r)
        self.assertEqual(first, second); self.agent.assert_called_once()
        changed = copy.deepcopy(r); d = changed['input']['planning_draft']
        d['raw_response']['result']['draft']['title'] = 'changed title'
        d['raw_response']['result']['draft_identity']['content_sha256'] = fingerprint(d['raw_response']['result']['draft'])
        d['content_sha256'] = fingerprint(d['raw_response'])
        with self.assertRaisesRegex(ReviewError, 'validation failed'):
            self.service.review(changed)

    def test_raw_artifact_verification_rejects_wrong_bytes(self):
        # Conventions section 8 codes: wrong length, then right length but wrong digest.
        with self.assertRaises(ReviewError) as error:
            parse_review_request(request(), artifact_resolver=lambda artifact: b'{}')
        self.assertEqual(error.exception.code, 'ARTIFACT_SIZE_MISMATCH')
        with self.assertRaises(ReviewError) as error:
            parse_review_request(request(), artifact_resolver=lambda artifact: b' ' * artifact['size_bytes'])
        self.assertEqual(error.exception.code, 'ARTIFACT_HASH_MISMATCH')

    def test_raw_artifact_bytes_are_verified_at_every_depth(self):
        def original_bytes(artifact):
            seen.append(artifact['uri'])
            return raw[artifact['sha256']]
        raw, seen = {}, []
        for node in retained_nodes(request()):
            data = (json.dumps(node['raw_response'], indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode()
            self.assertEqual(sha256(data).hexdigest(), node['raw_artifact']['sha256'])
            raw[node['raw_artifact']['sha256']] = data
        parse_review_request(request(), artifact_resolver=original_bytes)
        self.assertEqual(len(seen), 3)
        swapped = dict(raw)
        draft_sha = request()['input']['planning_draft']['raw_artifact']['sha256']
        swapped[draft_sha] = canonical(json.loads(raw[draft_sha]) | {'status': 'partial'})
        with self.assertRaises(ReviewError):
            parse_review_request(request(), artifact_resolver=lambda a: swapped[a['sha256']])

    def test_domain_error_cannot_become_no_results(self):
        source = Mock(); source.research.return_value = SourceReport(status=ResultStatus.ERROR,
            sales_data=SalesDataResult('error', errors=('source unavailable',)))
        self.agent.return_value = MarketResearchAgent(source)
        code, response = self.post(request())
        self.assertEqual(code, 503); self.assertEqual(response['status'], 'error')
        self.assertTrue(response['result']['errors'])

    def test_invalid_source_metadata_is_502(self):
        source = Mock(); source.research.return_value = SourceReport(status=ResultStatus.OK,
            evidence=(EvidenceRecord('E1','test','test','Test only',permission_status=PermissionStatus.AUTHORIZED),),
            sales_data=SalesDataResult('ok', observations=(SalesObservation('o','sales',1,'rows',('E1',),
                as_of='2020-01-01', currency='invalid-currency'),)))
        self.agent.return_value = MarketResearchAgent(source)
        code, response = self.post(request())
        self.assertEqual(code, 502); self.assertEqual(response['error']['code'], 'INVALID_SOURCE_RESPONSE')

    def test_local_bind_and_service_token_guard(self):
        with self.assertRaises(ValueError): serve(host='0.0.0.0')
        with patch.dict(os.environ,{},clear=True), self.assertRaises(ValueError):serve(host='0.0.0.0',service_mode=True)

if __name__=='__main__':unittest.main()
