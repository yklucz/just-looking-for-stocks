"""Explicitly bind an existing trusted artifact; never trains or selects by score."""
import argparse
import json
from pathlib import Path

from .artifact_loader import ROOT, load_bound_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=ROOT/'config/prediction_models.json')
    args = parser.parse_args()
    path = args.artifact.resolve()
    metadata = json.loads((path/'metadata.json').read_text())
    saved = json.loads(args.manifest.read_text()) if args.manifest.exists() else {'version': 1, 'bindings': {}}
    saved['bindings'][f'{args.ticker.upper()}:xgboost'] = {
        'artifact': str(path), 'fingerprint': metadata['fingerprint'], 'model_sha256': metadata['model_sha256']}
    temporary = args.manifest.with_suffix('.pending.json')
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(saved, indent=2))
    try:
        load_bound_model(args.ticker.upper(), 'xgboost', temporary)
        temporary.replace(args.manifest)
    finally:
        temporary.unlink(missing_ok=True)
    print(args.manifest)


if __name__ == '__main__':
    main()
