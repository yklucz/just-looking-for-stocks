"""Single-split GRU smoke/research comparison using the shared Stage 3 runner."""
import argparse
import logging
from pathlib import Path

from ..config import FeatureConfig, TargetConfig
from ..models.gru_model import GRUClassifier
from .train_baselines import run_research


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--interval', default='1d')
    parser.add_argument('--horizon', type=int, default=5)
    parser.add_argument('--target-threshold', type=float, default=TargetConfig().threshold)
    parser.add_argument('--artifact', type=Path, help='Use an existing frozen Stage 4 market snapshot')
    parser.add_argument('--output', type=Path, default=Path('artifacts'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if args.artifact:
        from ..backtest.inputs import load_frozen_inputs
        frozen = load_frozen_inputs(args.artifact, ticker=args.ticker)
        history, source = frozen.market, f'Frozen Stage 4 snapshot {frozen.path}'
        if args.interval != frozen.configuration['interval']:
            parser.error('Snapshot interval mismatch')
    else:
        if args.interval != '1d':
            parser.error('Live research retrieval currently supports daily data only')
        from ..stock_service import _get_full_history
        history, source = _get_full_history(args.ticker), 'Yahoo daily full-history cache'
    report = run_research(history, args.ticker, args.output, FeatureConfig(interval=args.interval),
                          TargetConfig(horizon=args.horizon, threshold=args.target_threshold, task='binary'),
                          source=source, additional_factories=(GRUClassifier,))
    print(report['report_path'])


if __name__ == '__main__':
    main()
