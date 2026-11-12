"""Check/sync source-owned configs/web/prompts for installed package runtime.

The vendored workflow contract is never synced or rewritten here; both modes
only verify its schema bytes against the SHA-256 pin manifest (snapshot.json).
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/'src/market_research_agent/resources/workflow_contract'


def contract_pin_errors(root=CONTRACT):
    """List mismatches between vendored *.schema.json bytes and snapshot.json."""
    try:
        files=json.loads((root/'snapshot.json').read_bytes())['files']
        if not isinstance(files,dict) or not files: raise ValueError
    except (OSError,ValueError,KeyError,TypeError):
        return ['snapshot.json is missing or invalid']
    present={p.relative_to(root).as_posix() for p in root.rglob('*.schema.json')}
    errors=[f'{name} is not pinned' for name in sorted(present-set(files))]
    for name,digest in sorted(files.items()):
        path=root/name
        if not path.is_file(): errors.append(f'{name} is missing')
        elif hashlib.sha256(path.read_bytes()).hexdigest()!=digest: errors.append(f'{name} bytes changed')
    return errors


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
    changed=[]
    for directory in ['configs','web','prompts']:
        for source in (ROOT/directory).rglob('*'):
            if source.is_file():
                target=ROOT/'src/market_research_agent/resources'/source.relative_to(ROOT)
                if not target.exists() or source.read_bytes()!=target.read_bytes():
                    changed.append(str(source.relative_to(ROOT)))
                    if not args.check:
                        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    pin=contract_pin_errors()
    if pin: raise SystemExit('SCHEMA_SNAPSHOT_CHANGED: '+'; '.join(pin))
    if args.check and changed: raise SystemExit('Stale package resources: '+', '.join(changed))
    print('Package resources match source assets; workflow contract matches its SHA-256 pin.')
