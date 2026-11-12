"""Check/sync source-owned configs/web/prompts for installed package runtime."""
import argparse
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[1]
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
    if args.check and changed: raise SystemExit('Stale package resources: '+', '.join(changed))
    print('Package resources match source assets.')
