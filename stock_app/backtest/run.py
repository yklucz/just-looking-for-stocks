"""Offline research from frozen Stage 4 OOF. No model fitting is performed."""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import logging
from pathlib import Path
from uuid import uuid4

from ..config import BacktestConfig
from ..models.registry import fingerprint
from .benchmarks import benchmark_simulations
from .diagnostics import estimated_cost_break_even, probability_buckets
from .engine import simulate
from .inputs import FrozenInputs, load_frozen_inputs
from .metrics import performance
from .report import write_report

logger = logging.getLogger(__name__)


def run_backtest(inputs: FrozenInputs, model: str = "xgboost", config: BacktestConfig = BacktestConfig(),
                 output: Path | None = None) -> dict:
    if model not in inputs.configuration['models']:
        raise ValueError("Model not present in frozen OOF")
    probabilities = inputs.oof[f'{model}_probability'].copy()
    # Only market prices and probability series reach the engine; realized targets do not.
    net = simulate(inputs.market, probabilities, config, model)
    zero = replace(config, commission_bps=0.0, slippage_bps=0.0)
    gross = simulate(inputs.market, probabilities, zero, model)
    benchmarks = benchmark_simulations(inputs.market, probabilities, config)
    gross_benchmarks = benchmark_simulations(inputs.market, probabilities, zero)
    buckets = probability_buckets(inputs.oof, model, config.bucket_edges)
    configuration = {'version': 'backtest-v1', 'ticker': inputs.configuration['ticker'], 'interval': '1d',
                     'target': inputs.configuration['target'], 'model': model, 'backtest': asdict(config),
                     'source': {'artifact': str(inputs.path), 'configuration_fingerprint': inputs.summary['configuration_fingerprint'],
                                'manifest_sha256': inputs.source_manifest_sha256,
                                'raw_data_sha256': inputs.summary.get('provenance', {}).get('raw_data_sha256')},
                     'price_convention': inputs.price_convention,
                     'execution': 'signal after close[t]; enter Open[t+1]; exit Open[t+1+holding_period]; ignore active-position signals; no same-open re-entry',
                     'equity': 'post-order Open marks; first return interval includes initial entry costs; fractional adjusted units; cash interest zero',
                     'benchmark': '100% Buy & Hold, same first executable OOF Open through final supported OOF exit Open; same per-side costs',
                     'turnover': 'sum of raw entry and exit notionals divided by initial capital',
                     'cost_break_even': 'first-order per-side all-in bps on fixed zero-cost quantities; no compounding correction'}
    summary = {'ticker': configuration['ticker'], 'model': model, 'mode': inputs.summary['mode'],
               'configuration_fingerprint': fingerprint(configuration),
               'net': performance(net, config), 'gross': performance(gross, zero),
               'benchmarks': {name: performance(sim, config) for name, sim in benchmarks.items()},
               'gross_benchmarks': {name: performance(sim, zero) for name, sim in gross_benchmarks.items()},
               'diagnostics': net.diagnostics, 'estimated_cost_break_even_bps_per_side': estimated_cost_break_even(gross.trades)}
    summary['excess_return_vs_buy_hold'] = summary['net']['total_return'] - summary['benchmarks']['buy_hold']['total_return']
    summary['excess_return_vs_cash'] = summary['net']['total_return']
    root = (Path(output) if output is not None else inputs.path / 'backtest') / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid4().hex[:8])
    summary['artifact_path'] = str(root.resolve())
    tables = {'trades': net.trades, 'equity': net.equity, 'gross_trades': gross.trades, 'gross_equity': gross.equity,
              'probability_buckets': buckets}
    for name, sim in benchmarks.items():
        tables[name+'_equity'] = sim.equity
        tables[name+'_trades'] = sim.trades
        tables['gross_'+name+'_equity'] = gross_benchmarks[name].equity
    write_report(root, configuration, summary, tables)
    logger.info('%s %s %s trades=%d return=%.4f saved=%s', summary['ticker'], summary['mode'], model,
                summary['net']['trade_count'], summary['net']['total_return'], root)
    return summary


def main() -> None:
    defaults = BacktestConfig()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--model', default='xgboost')
    parser.add_argument('--threshold', type=float, default=defaults.decision_threshold)
    parser.add_argument('--holding-period', type=int, default=defaults.holding_period)
    parser.add_argument('--initial-capital', type=float, default=defaults.initial_capital)
    parser.add_argument('--commission-bps', type=float, default=defaults.commission_bps)
    parser.add_argument('--slippage-bps', type=float, default=defaults.slippage_bps)
    parser.add_argument('--position-size', type=float, default=defaults.position_size)
    parser.add_argument('--price-convention', choices=['auto_adjusted_ohlc'])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        inputs = load_frozen_inputs(args.artifact, price_convention=args.price_convention)
        config = BacktestConfig(initial_capital=args.initial_capital, decision_threshold=args.threshold,
                                holding_period=args.holding_period, commission_bps=args.commission_bps,
                                slippage_bps=args.slippage_bps, position_size=args.position_size)
        result = run_backtest(inputs, args.model, config, args.output)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'Backtest failed: {exc}\n')
    print(result['artifact_path'])


if __name__ == '__main__':
    main()
