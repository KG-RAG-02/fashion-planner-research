"""The vendored workflow contract is pinned by SHA-256 and fails closed when changed."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from market_research_agent.resource_paths import resource_root
from market_research_agent.reviews import ReviewError, load_pinned_schemas

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = resource_root('workflow_contract')
# sha256 of vogue-orchestrator/contracts/workflow/snapshot.json for
# workflow-contract/0.1.1 (equals baseline.json "snapshot_sha256").
CONTRACT_PACKAGE_PIN_SHA256 = 'cc4cf99533b709efa78e7fa4f4834242c17227fa8c9a88cfbafaf5572b94d4fe'
PINNED = {
    'common.schema.json', 'integrated-result.schema.json',
    'planning-draft-request.schema.json', 'planning-draft-response.schema.json',
    'sales-market-review-request.schema.json', 'sales-market-review-response.schema.json',
    'upstream/stock-result-1.0.schema.json', 'upstream/trend-result-1.1.schema.json',
}


def load_sync_script():
    spec = importlib.util.spec_from_file_location('sync_package_resources', ROOT / 'scripts/sync_package_resources.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkflowContractPinTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.copy = Path(temp.name) / 'workflow_contract'
        shutil.copytree(CONTRACT, self.copy)

    def assert_changed(self, root=None):
        with self.assertRaises(ReviewError) as caught:
            load_pinned_schemas(root or self.copy)
        self.assertEqual(caught.exception.code, 'SCHEMA_SNAPSHOT_CHANGED')
        self.assertTrue(load_sync_script().contract_pin_errors(root or self.copy))

    def test_pin_is_the_contract_package_snapshot_and_matches_vendored_bytes(self):
        raw = (CONTRACT / 'snapshot.json').read_bytes()
        self.assertEqual(sha256(raw).hexdigest(), CONTRACT_PACKAGE_PIN_SHA256)
        pin = json.loads(raw)
        self.assertEqual((pin['contract_version'], pin['transport_version']),
                         ('workflow-contract/0.1.1', 'vogue-workflow/0.1'))
        self.assertEqual(set(pin['files']), PINNED)
        for name, digest in pin['files'].items():
            self.assertEqual(sha256((CONTRACT / name).read_bytes()).hexdigest(), digest, name)
        self.assertEqual(set(load_pinned_schemas(CONTRACT)), PINNED)
        self.assertEqual(load_sync_script().contract_pin_errors(CONTRACT), [])

    def test_one_changed_byte_fails_closed(self):
        path = self.copy / 'sales-market-review-request.schema.json'
        path.write_bytes(path.read_bytes() + b' ')
        self.assert_changed()

    def test_unpinned_missing_or_invalid_files_fail_closed(self):
        (self.copy / 'extra.schema.json').write_bytes(b'{}')
        self.assert_changed()
        (self.copy / 'extra.schema.json').unlink()
        (self.copy / 'upstream/trend-result-1.1.schema.json').unlink()
        self.assert_changed()
        shutil.copy2(CONTRACT / 'upstream/trend-result-1.1.schema.json', self.copy / 'upstream/trend-result-1.1.schema.json')
        load_pinned_schemas(self.copy)
        (self.copy / 'snapshot.json').write_bytes(b'{"files": {}, "files": {}}')
        self.assert_changed()
        (self.copy / 'snapshot.json').unlink()
        self.assert_changed()

    def test_sync_check_verifies_the_pin_without_rewriting_the_contract(self):
        before = {p.relative_to(CONTRACT): p.read_bytes() for p in CONTRACT.rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/sync_package_resources.py'), '--check'],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('workflow contract matches its SHA-256 pin', result.stdout)
        self.assertEqual({p.relative_to(CONTRACT): p.read_bytes() for p in CONTRACT.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
