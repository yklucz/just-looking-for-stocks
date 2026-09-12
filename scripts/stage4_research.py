"""Predeclared AAPL/SPY window comparison and one matched-origin AAPL SMA200 ablation."""
import argparse
from dataclasses import replace
import json
import logging
from pathlib import Path
from uuid import uuid4

from stock_app.config import FeatureConfig, TargetConfig, WalkForwardConfig
from stock_app.features import build_features
from stock_app.stock_service import _get_full_history
from stock_app.training.walk_forward import run_walk_forward


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('artifacts'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    target = TargetConfig(horizon=5, threshold=.002, task='binary')
    full = FeatureConfig()
    short = replace(full, sma_windows=tuple(n for n in full.sma_windows if n != 200))
    runs = {}
    failures = {}
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / f'stage4_comparison_{uuid4().hex[:8]}.json'
    for ticker in ('AAPL','SPY','MSFT'):
        if ticker == 'MSFT' and 'SPY_expanding' in runs:
            break
        try:
            raw = _get_full_history(ticker)
            # Do not repair/ignore provider OHLC violations to force an experiment.
            common = build_features(raw, full).frame.index
        except Exception as exc:
            failures[ticker] = str(exc)
            logging.error('%s data unavailable for research: %s', ticker, exc)
            if isinstance(exc, ValueError) and 'OHLC' in str(exc):
                rejected = args.output / f'stage4_rejected_{ticker}_{uuid4().hex[:8]}.csv'
                raw.to_csv(rejected, index_label='timestamp')
                failures[ticker] += f'; rejected snapshot: {rejected}'
            continue
        # Feature-validity matching is based solely on causal inputs, never outcomes.
        source = 'Yahoo daily full-history cache; fixed Stage 4 protocol'
        for mode in ('expanding','rolling'):
            runs[f'{ticker}_{mode}'] = run_walk_forward(raw, ticker, args.output,
                WalkForwardConfig(mode=mode), full, target, eligible_index=common, source=source)
            path.write_text(json.dumps({'runs': runs, 'retrieval_failures': failures}, indent=2, allow_nan=False))
        if ticker == 'AAPL':
            runs['AAPL_no_sma200_matched'] = run_walk_forward(raw, ticker, args.output,
                WalkForwardConfig(), short, target, eligible_index=common, source=source)
    if not runs:
        raise SystemExit('No asset experiment succeeded; no results fabricated')
    path.write_text(json.dumps({'protocol': 'Fixed Stage 3 parameters; h5 event .002; cutoff .5; matched-origin ablation',
                                'runs': runs, 'retrieval_failures': failures}, indent=2, allow_nan=False))
    print(json.dumps({'comparison_path': str(path.resolve()), 'runs': {name: s['artifact_path'] for name,s in runs.items()},
                      'retrieval_failures': failures}, indent=2))


if __name__ == '__main__':
    main()
