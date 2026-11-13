"""Independent full-Draft review boundary for the proposed workflow/0.1.

No provider/model calls occur while parsing. Raw transport bytes and parsed
snapshots are separately content addressed. Source reports never pass through
the legacy no_results normalizer or proposal serializers.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .domain.models import MarketResearchContext
from .resource_paths import resource_root

VERSION = 'vogue-workflow/0.1'
HASH_PROFILE = 'python-json-sort/1'
MAX_BODY_BYTES = 8_388_608
REVIEW_PATH = '/v1/agents/sales-market-agent/reviews'


class ReviewError(ValueError):
    def __init__(self, code, message, http_status=422):
        super().__init__(message)
        self.code, self.http_status = code, http_status


def canonical(value):
    def keys(v):
        if isinstance(v, dict):
            if any(not isinstance(k, str) for k in v):
                raise ValueError('Non-string key')
            for x in v.values(): keys(x)
        elif isinstance(v, (list, tuple)):
            for x in v: keys(x)
    keys(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def fingerprint(value):
    return sha256(canonical(value)).hexdigest()


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate key')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        canonical(value)  # also reject exponent overflow to Infinity
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ReviewError('INVALID_JSON', 'Valid UTF-8 JSON with unique keys and finite numbers is required.', 400) from None


CONTRACT_PIN = 'snapshot.json'


def load_pinned_schemas(root):
    """Return the vendored schemas only if their exact bytes match the SHA-256 pin.

    ``snapshot.json`` is a byte-identical copy of the coordinator's
    contracts/workflow/snapshot.json. Any changed, missing or unpinned schema
    file fails closed with SCHEMA_SNAPSHOT_CHANGED.
    """
    root = Path(root)
    def changed(detail):
        return ReviewError('SCHEMA_SNAPSHOT_CHANGED',
                           'SCHEMA_SNAPSHOT_CHANGED: vendored workflow contract ' + detail + '.', 500)
    try:
        pin = parse_json((root / CONTRACT_PIN).read_bytes())
    except (OSError, ReviewError):
        raise changed('pin manifest is missing or invalid') from None
    files = pin.get('files') if isinstance(pin, dict) else None
    if (not isinstance(files, dict) or not files or pin.get('transport_version') != VERSION
            or any(not isinstance(d, str) or len(d) != 64 or d.strip('0123456789abcdef') for d in files.values())):
        raise changed('pin manifest has an unsupported shape')
    present = {p.relative_to(root).as_posix() for p in root.rglob('*.schema.json')}
    if present != set(files):
        raise changed('schema files differ from the pinned set')
    schemas = {}
    for name, digest in sorted(files.items()):
        try:
            raw = (root / name).read_bytes()
        except OSError:
            raise changed(name + ' is unreadable') from None
        if sha256(raw).hexdigest() != digest:
            raise changed(name + ' bytes do not match the pin')
        try:
            schemas[name] = parse_json(raw)
        except ReviewError:
            raise changed(name + ' is not valid JSON') from None
    return schemas


_SCHEMAS = load_pinned_schemas(resource_root('workflow_contract'))
_REGISTRY = Registry().with_resources((uri, Resource.from_contents(v))
    for name, v in _SCHEMAS.items()
    for uri in {v.get('$id', ''), 'https://contracts.vogue.invalid/workflow/0.1/' + name} if uri)


def validate_schema(value, name):
    try:
        canonical(value)
        Draft202012Validator(_SCHEMAS[name], registry=_REGISTRY,
                             format_checker=FormatChecker()).validate(value)
    except Exception:
        # Do not echo offending values (may be user/private data).
        raise ReviewError('INVALID_CONTRACT', 'Input does not satisfy the pinned workflow schema.') from None


def require(ok, code):
    if not ok: raise ReviewError(code, 'Workflow identity, provenance or state validation failed.')


def pointer(doc, path):
    try:
        for part in path.split('/')[1:]:
            part = part.replace('~1', '/').replace('~0', '~')
            doc = doc[int(part)] if isinstance(doc, list) else doc[part]
        return doc
    except (KeyError, IndexError, ValueError, TypeError):
        raise ReviewError('INVALID_POINTER', 'Provenance pointer does not resolve.') from None


def validate_envelope(doc, request_id, agent, schema):
    result = doc['result']
    require(doc['contract_version'] == VERSION, 'WRONG_VERSION')
    require(doc['request_id'] == result['request_id'] == request_id, 'REQUEST_ID_MISMATCH')
    require(doc['producer'] == {'agent_id': agent, 'schema_version': schema}, 'WRONG_PRODUCER')
    require(result['agent_id'] == agent and result['schema_version'] == schema, 'WRONG_SCHEMA')
    require(doc['status'] == result['status'], 'STATUS_MISMATCH')
    require(not result.get('errors') or doc['status'] in {'partial', 'error'}, 'ERROR_DOWNGRADED')


def validate_retained(retained, request_id):
    doc = retained['raw_response']
    require(retained['hash_profile'] == HASH_PROFILE, 'WRONG_HASH_PROFILE')
    require(fingerprint(doc) == retained['content_sha256'], 'SOURCE_HASH_MISMATCH')
    require(doc['request_id'] == request_id, 'REQUEST_ID_MISMATCH')
    if isinstance(doc.get('result'), dict):
        require(doc['result'].get('request_id') == request_id, 'REQUEST_ID_MISMATCH')
        require(doc.get('status') == doc['result'].get('status'), 'STATUS_MISMATCH')
    return doc


def check_refs(value, documents):
    if isinstance(value, dict):
        if 'evidence_refs' in value:
            require(value.get('kind') != 'fact' or value['evidence_refs'], 'UNSUPPORTED_FACT')
            for ref in value['evidence_refs']:
                source = documents.get(ref['source_response_sha256'])
                require(source is not None, 'EVIDENCE_DOCUMENT_MISSING')
                item = pointer(source, ref['source_pointer'])
                require(isinstance(item, dict) and item.get('evidence_id') == ref['evidence_id'], 'EVIDENCE_REFERENCE_MISMATCH')
        for child in value.values(): check_refs(child, documents)
    elif isinstance(value, list):
        for child in value: check_refs(child, documents)


def retained_nodes(value):
    """Yield every retained response (raw_response + raw_artifact) at any depth."""
    if isinstance(value, dict):
        if 'raw_response' in value and 'raw_artifact' in value:
            yield value
        for child in value.values(): yield from retained_nodes(child)
    elif isinstance(value, list):
        for child in value: yield from retained_nodes(child)


def verify_raw_artifact(node, artifact_resolver):
    """Exact retained bytes must match their reference and parse to raw_response."""
    ref = node['raw_artifact']; raw = artifact_resolver(ref)
    if not isinstance(raw, bytes) or len(raw) != ref['size_bytes']:
        raise ReviewError('ARTIFACT_SIZE_MISMATCH', 'Retained raw artifact size does not match its reference.')
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ReviewError('ARTIFACT_HASH_MISMATCH', 'Retained raw artifact SHA-256 does not match its reference.')
    try:
        parsed = parse_json(raw)
    except ReviewError:
        raise ReviewError('RAW_ARTIFACT_OBJECT_MISMATCH', 'Retained raw artifact is not the retained JSON response.') from None
    require(canonical(parsed) == canonical(node['raw_response']), 'RAW_ARTIFACT_OBJECT_MISMATCH')


def parse_review_request(value, artifact_resolver=None):
    """Return a detached full snapshot, plus separately derived search provenance.

    With an ``artifact_resolver`` every retained response's exact raw bytes
    are read and verified; without one, raw_artifact refs are only retained.
    """
    validate_schema(value, 'sales-market-review-request.schema.json')
    value = deepcopy(value)
    if artifact_resolver is not None:
        for node in retained_nodes(value): verify_raw_artifact(node, artifact_resolver)
    request_id = value['request_id']
    retained = value['input']['planning_draft']
    doc = validate_retained(retained, request_id)
    validate_envelope(doc, request_id, 'new-product-agent', 'planning-draft/1.0')
    require(doc['status'] in {'ok', 'partial'}, 'DRAFT_NOT_REVIEWABLE')
    result = doc['result']
    require(fingerprint(result['draft']) == result['draft_identity']['content_sha256'], 'DRAFT_HASH_MISMATCH')
    source = result['source_request']
    require(source['request_id'] == request_id, 'REQUEST_ID_MISMATCH')
    require(fingerprint(source) == result['source_request_sha256'], 'SOURCE_REQUEST_HASH_MISMATCH')
    inp = source['input']
    require(inp['planning_request']['request_id'] == request_id, 'REQUEST_ID_MISMATCH')
    trend = validate_retained(inp['trend_response'], request_id)
    stock = validate_retained(inp['stock_response'], request_id)
    require(trend['producer'] == {'agent_id': 'trend-analysis-agent', 'schema_version': '1.1'}, 'WRONG_UPSTREAM_PRODUCER')
    require(stock['producer'] == {'agent_id': 'stock-agent', 'schema_version': '1.0'}, 'WRONG_UPSTREAM_PRODUCER')
    require(trend['status'] == 'ok' and stock['status'] in {'ok', 'partial', 'no_results'}, 'UPSTREAM_TERMINAL')
    require(stock['result']['input_provenance']['source_payload_sha256'] == fingerprint(trend['result']), 'STOCK_TREND_HASH_MISMATCH')
    require(inp['planning_request']['query'] == inp['user_input']['text'], 'USER_TEXT_MISMATCH')
    expected_context = {'planning_request_sha256': fingerprint(inp['planning_request']),
                        'trend_response_sha256': fingerprint(trend), 'stock_input_sha256': fingerprint(trend['result']),
                        'stock_wire_request_sha256': fingerprint({'contract_version': 'vogue-handoff/0.1', 'request_id': request_id, 'input': trend['result']}),
                        'stock_response_sha256': fingerprint(stock)}
    require(inp['stock_call_context'] == expected_context, 'STOCK_CONTEXT_MISMATCH')
    trend_result = trend['result']; evs = trend_result['evidence']
    ids = {e['evidence_id'] for e in evs}
    require(trend_result['trends'] and evs and len(ids) == len(evs), 'INVALID_TREND_EVIDENCE')
    for e in evs:
        expected_id = f"trend:{e['article_id']}:{e['block_idx']}:{sha256(e['quote'].encode('utf-8')).hexdigest()[:8]}"
        require(e['evidence_id'] == expected_id, 'TREND_EVIDENCE_HASH_MISMATCH')
    def links(v):
        if isinstance(v, dict):
            for key in ('evidence_ids', 'focus_evidence_ids'):
                require(set(v.get(key, [])) <= ids, 'DANGLING_TREND_EVIDENCE')
            for child in v.values(): links(child)
        elif isinstance(v, list):
            for child in v: links(child)
    links(trend_result['trends'])
    documents = {fingerprint(trend): trend, fingerprint(stock): stock}
    check_refs(result['draft'], documents)
    # The search projection cannot substitute or reinterpret any source field.
    derived = value['input']['derived_view']
    if derived is None:
        derived = {'purpose': 'search_only', 'adapter_version': 'p07-draft-search/1.0',
                   'source_response_sha256': retained['content_sha256'],
                   'source_pointers': ['/result/draft/category', '/result/draft/season'],
                   'product': {k: result['draft'][k] for k in ('category', 'season')}}
    require(derived['source_response_sha256'] == retained['content_sha256'], 'DERIVED_HASH_MISMATCH')
    for key in ('category', 'season'):
        path = '/result/draft/' + key
        require(path in derived['source_pointers'] and derived['product'][key] == pointer(doc, path), 'DERIVED_VALUE_MISMATCH')
    for path in derived['source_pointers']: pointer(doc, path)
    return value, deepcopy(derived)


# contract_version -> request parser. Dispatch happens before schema,
# artifact, store or source work; unknown versions are never parsed.
_REQUEST_PARSERS = {VERSION: parse_review_request}


def parser_for(body):
    """Return the parser for the body's contract_version or fail with 422."""
    version = body.get('contract_version') if isinstance(body, dict) else None
    parser = _REQUEST_PARSERS.get(version) if isinstance(version, str) else None
    if parser is None:
        raise ReviewError('UNSUPPORTED_CONTRACT_VERSION',
                          'Unsupported contract_version; this route accepts vogue-workflow/0.1.', 422)
    return parser


REVIEW_ARTIFACT_DIR_ENV = 'VOGUE_P07_REVIEW_ARTIFACT_DIR'
DEFAULT_REVIEW_ARTIFACT_DIR = '.local/review-artifacts'


def review_artifact_dir(environ=None):
    """Store root for artifact://p07/reviews/<sha256>.json and reviews.sqlite3.

    Unset or blank keeps the historical default, relative to the working
    directory; a blank value never means the working directory itself.
    """
    value = (os.environ if environ is None else environ).get(REVIEW_ARTIFACT_DIR_ENV, '').strip()
    return Path(value or DEFAULT_REVIEW_ARTIFACT_DIR)


class ReviewArtifactStore:
    """Private immutable local artifacts; no paths/bytes are served over HTTP."""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def put(self, raw):
        digest = sha256(raw).hexdigest()
        path = self.root / (digest + '.json')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as f:
                temporary = Path(f.name)
                f.write(raw); f.flush(); os.fsync(f.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                require(path.read_bytes() == raw, 'ARTIFACT_COLLISION')
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
        return {'artifact_id': 'p07:' + digest, 'uri': 'artifact://p07/reviews/' + path.name,
                'sha256': digest, 'media_type': 'application/json', 'size_bytes': len(raw), 'immutable': True}


def _observation(item, now, source):
    synthetic = item.get('synthetic')
    nature = item.get('data_nature')
    if nature == 'hm_kaggle_historical': nature = 'historical_external'
    if synthetic: nature = 'synthetic_fixture'
    elif nature not in {'historical_external', 'current_internal', 'sample', 'user_provided'}:
        nature = 'historical_external' if 'hm' in str(item.get('source_id', '')).lower() else 'unknown'
    return {'observed_at': item.get('observed_at') or item.get('fetched_at') or now,
            'as_of': item.get('as_of'), 'synthetic': synthetic, 'data_nature': nature,
            'source': source, 'limitations': ([] if nature == 'current_internal' else
                ['This source does not establish current company sales or current market demand.'])}


def parse_review_response(value, request):
    validate_schema(value, 'sales-market-review-response.schema.json')
    snapshot, _ = parse_review_request(request)
    validate_envelope(value, snapshot['request_id'], 'sales-market-agent', 'sales-market-review/1.0')
    r = value['result']; review = r['review']
    draft = snapshot['input']['planning_draft']
    require(review['reviewed_draft'] == draft['raw_response']['result']['draft_identity'], 'DRAFT_IDENTITY_MISMATCH')
    require(review['draft_response_sha256'] == draft['content_sha256'], 'DRAFT_RESPONSE_HASH_MISMATCH')
    require(r['review_identity']['content_sha256'] == fingerprint(review), 'REVIEW_HASH_MISMATCH')
    src = draft['raw_response']['result']['source_request']['input']
    documents = {x['content_sha256']: x['raw_response'] for x in
                 (draft, src['trend_response'], src['stock_response'])}
    for item in review['evidence']:
        if item.get('record_type') == 'p07_source_snapshot':
            require(fingerprint(item['source_data']) == item['source_response_sha256'], 'SOURCE_HASH_MISMATCH')
            require(item['retained_planning_draft'] == draft, 'RETAINED_DRAFT_CHANGED')
            documents[item['source_response_sha256']] = item['source_data']
    check_refs(review['findings'], documents)
    check_refs(review['metrics'], documents)
    require(value['status'] not in {'ok', 'partial'} or any(f['evidence_refs'] for f in review['findings']) or
            any(m['value'] is not None for m in review['metrics']), 'NO_ELIGIBLE_BASIS')
    for finding in review['findings']:
        require(finding['kind'] != 'fact' or bool(finding['evidence_refs']), 'UNSUPPORTED_FACT')
    return deepcopy(value)


class SalesMarketReviewService:
    def __init__(self, agent_factory, artifact_store, artifact_resolver=None):
        self.agent_factory, self.store = agent_factory, artifact_store
        self.artifact_resolver = artifact_resolver

    def review(self, request, raw_body=None):
        snapshot, _ = parser_for(request)(request, self.artifact_resolver)
        # Persist every received transport, including formatting variants/replays.
        self.store.put(raw_body if raw_body is not None else canonical(snapshot))
        identity = snapshot['input']['planning_draft']['raw_response']['result']['draft_identity']
        key = fingerprint(snapshot)
        # closing() releases the handle; the inner "db" context commits or rolls back.
        with closing(sqlite3.connect(self.store.root / 'reviews.sqlite3', timeout=30)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS identities (draft_id TEXT, revision INTEGER, hash TEXT, PRIMARY KEY(draft_id, revision))')
            db.execute('CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, code INTEGER, response BLOB)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT hash FROM identities WHERE draft_id=? AND revision=?', (identity['draft_id'], identity['revision'])).fetchone()
            if row is not None and row[0] != identity['content_sha256']:
                # Conventions section 6: a revision conflict is a 409 state conflict.
                raise ReviewError('DRAFT_REVISION_CONFLICT',
                                  'Draft identity validation failed: this draft_id and revision were already '
                                  'reviewed with different Draft content.', 409)
            db.execute('INSERT OR IGNORE INTO identities VALUES (?,?,?)', (identity['draft_id'], identity['revision'], identity['content_sha256']))
            cached = db.execute('SELECT code, response FROM responses WHERE key=?', (key,)).fetchone()
            if cached:
                return cached[0], parse_review_response(parse_json(cached[1]), snapshot)
            code, response = self._evaluate(snapshot, raw_body, artifacts_verified=self.artifact_resolver is not None)
            if code == 200:
                db.execute('INSERT INTO responses VALUES (?,?,?)', (key, code, canonical(response)))
            return code, response

    def _evaluate(self, request, raw_body=None, artifacts_verified=False):
        snapshot, derived = parse_review_request(request)
        retained_count = sum(1 for _ in retained_nodes(snapshot))
        artifact_note = (f'Incoming raw_artifact bytes ({retained_count} retained responses) were read from the '
                         'read-only upstream artifact mount and verified by size, SHA-256 and parsed JSON before review.'
                         if artifacts_verified else
                         'Incoming P08 raw_artifact refs are retained; their remote bytes were not fetched by P07.')
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        retained = snapshot['input']['planning_draft']; doc = retained['raw_response']
        # Persist exact received request bytes AND a canonical parsed Draft snapshot.
        artifacts = [self.store.put(raw_body if raw_body is not None else canonical(snapshot)),
                     self.store.put(canonical(doc)), self.store.put(canonical(derived))]
        context = MarketResearchContext(request_id=snapshot['request_id'],
                    planning_request={'schema_version': '1.0', 'request_id': snapshot['request_id'],
                                      'query': doc['result']['draft']['title']},
                    requested_category=derived['product']['category'],
                    explicit_scope=deepcopy(derived['product']))
        result = self.agent_factory().run(context)
        # Full dataclass snapshot retains all 1.1/1.2/1.3 details and original statuses.
        details = json.loads(canonical(asdict(result)))
        source_artifact = self.store.put(canonical(details)); artifacts.append(source_artifact)
        source_hash = fingerprint(details)
        evidence = deepcopy(details['evidence'])
        index = {e['evidence_id']: i for i, e in enumerate(evidence)}
        refs = lambda ids: [{'evidence_id': eid, 'source_response_sha256': source_hash,
                            'source_pointer': '/evidence/' + str(index[eid])} for eid in ids if eid in index]
        limitations = list(details['limitations']) + list(doc['result']['limitations']) + [
            'Current company sales and current market source are not verified.',
            artifact_note,
            'Draft season is retained in search provenance; the legacy source adapter does not apply a season filter.',
            'Price, cost, quantity, purchase order and adoption require MD judgment.']
        metrics = []
        for domain in ('sales_data', 'market_data'):
            block = details.get(domain)
            if block:
                limitations.extend(block.get('limitations', []))
                for item in block.get('observations', []):
                    evrefs = refs(item.get('evidence_ids', []))
                    obs = _observation(item, now, domain)
                    # Untraceable values are retained only in the raw source artifact.
                    usable = bool(evrefs) and (item.get('as_of') or item.get('period_start')) and item.get('unit')
                    metrics.append({'name': domain + '.' + item['metric_name'],
                                    'value': item['value'] if usable else None,
                                    'missing_reason': None if usable else 'Evidence, period or unit is missing.',
                                    'unit': item.get('unit') or 'unknown', 'currency': item.get('currency'),
                                    'period': '/'.join(str(item.get(k) or '') for k in ('period_start', 'period_end')) if item.get('period_start') else item.get('as_of'),
                                    'comparability': 'unknown', 'observation': obs, 'evidence_refs': evrefs})
        unknown = _observation({}, now, 'No verified current company sales source')
        metrics.append({'name': 'current_company_sales', 'value': None,
                        'missing_reason': 'No verified current company sales source is connected.',
                        'unit': 'pieces', 'currency': None, 'period': None, 'comparability': 'unknown',
                        'observation': unknown, 'evidence_refs': []})
        findings = [{'kind': f['kind'], 'text': f['statement'], 'evidence_refs': refs(f['evidence_ids'])}
                    for f in details['findings'] if f['kind'] in {'fact', 'interpretation', 'proposal'} and refs(f['evidence_ids'])]
        errors = [{'code': e['code'], 'message': e['message'], 'stage': 'sales_market_review',
                   'fatal': True, 'retryable': e['code'] in {'TIMEOUT', 'SOURCE_UNAVAILABLE'}} for e in details['errors']]
        if any(details.get(k) and (details[k].get('errors') or details[k].get('status') == 'error') for k in ('sales_data', 'market_data', 'unit_economics')) or details['status'] == 'error':
            if not errors:
                errors.append({'code': 'SOURCE_UNAVAILABLE', 'message': 'A source domain reported an error; details are retained in its artifact.',
                               'stage': 'sales_market_review', 'fatal': True, 'retryable': True})
        has_results = bool(findings or any(m['value'] is not None for m in metrics))
        # Deliverable-based status (2026-11-13 decision): a review that produced findings or metrics is ok;
        # data gaps (null metrics, historical/sample sources) stay in limitations and metric missing_reason.
        status = 'error' if errors else ('ok' if has_results else 'no_results')
        if status == 'error': findings = []
        if not has_results: limitations.append('No usable source results; missing values are null, never inferred zero.')
        limitations = list(dict.fromkeys(limitations))
        # Inline full source snapshot/provenance keeps detailed data self-contained.
        evidence.append({'record_type': 'p07_source_snapshot', 'source_response_sha256': source_hash,
                         'source_data': details, 'artifact': source_artifact,
                         'derived_view': derived, 'retained_planning_draft': retained})
        nature = {m['observation']['data_nature'] for m in metrics if m['value'] is not None}
        observation = {**unknown, 'source': 'P07 review source snapshots',
                       'data_nature': next(iter(nature)) if len(nature) == 1 else ('mixed' if nature else 'unknown'),
                       'synthetic': True if nature == {'synthetic_fixture'} else None}
        review = {'reviewed_draft': deepcopy(doc['result']['draft_identity']),
                  'draft_response_sha256': retained['content_sha256'], 'findings': findings,
                  'evidence': evidence, 'metrics': metrics, 'limitations': limitations, 'observation': observation}
        response = {'contract_version': VERSION, 'request_id': snapshot['request_id'],
                    'producer': {'agent_id': 'sales-market-agent', 'schema_version': 'sales-market-review/1.0'},
                    'status': status, 'result': {'schema_version': 'sales-market-review/1.0',
                    'agent_id': 'sales-market-agent', 'request_id': snapshot['request_id'], 'status': status,
                    'review_identity': {'review_id': 'review:' + uuid4().hex, 'revision': 1, 'content_sha256': fingerprint(review)},
                    'review': review, 'limitations': limitations, 'errors': errors, 'artifacts': artifacts}}
        if len(canonical(response)) > 16_777_216:
            raise ReviewError('RESPONSE_TOO_LARGE', 'Review exceeds the response byte limit; full artifacts are retained.', 502)
        try:
            parse_review_response(response, snapshot)
        except ReviewError:
            raise ReviewError('INVALID_SOURCE_RESPONSE', 'Source output does not satisfy the review contract.', 502) from None
        code = 504 if any(e['code'] == 'TIMEOUT' for e in errors) else (503 if errors else 200)
        return code, response


def problem(code, message, request_id=None, retryable=False):
    return {'contract_version': VERSION, 'request_id': request_id, 'status': 'error',
            'error': {'code': code, 'message': message, 'stage': 'sales_market_review', 'fatal': True, 'retryable': retryable}}
