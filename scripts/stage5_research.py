"""Predeclared frozen-OOF protocol: four sources, four policies, all four costs."""
import argparse
from dataclasses import replace
import json
import logging
from pathlib import Path
from uuid import uuid4

from stock_app.config import BACKTEST_COST_SCENARIOS, BacktestConfig
from stock_app.backtest.inputs import load_frozen_inputs
from stock_app.backtest.run import run_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison', type=Path, default=Path('artifacts/stage4_comparison_1053262b.json'))
    parser.add_argument('--output', type=Path, default=Path('artifacts'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    sources = json.loads(args.comparison.read_text())['runs']
    rows = []
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / f'stage5_comparison_{uuid4().hex[:8]}.json'
    for name in ('AAPL_expanding', 'AAPL_rolling', 'SPY_expanding', 'SPY_rolling'):
        original = sources[name]
        inputs = load_frozen_inputs(Path(original['artifact_path']), expected_fingerprint=original['configuration_fingerprint'])
        if inputs.configuration['target'] != {'horizon': 5, 'threshold': .002, 'task': 'binary'}:
            raise ValueError('Fixed Stage 5 experiment requires h5 / .002 binary target')
        for model in ('xgboost', 'logistic', 'momentum', 'training_prior'):
            for scenario, (commission, slippage) in BACKTEST_COST_SCENARIOS.items():
                cfg = replace(BacktestConfig(), commission_bps=commission, slippage_bps=slippage)
                result = run_backtest(inputs, model, cfg)
                rows.append({'source': name, 'scenario': scenario, **result})
                destination.write_text(json.dumps({'protocol': 'Frozen probabilities; threshold .5; hold 5; long/flat; size 1; all predefined costs; no selection',
                                                    'runs': rows}, indent=2, allow_nan=False))
    print(destination.resolve())


if __name__ == '__main__':
    main()
