"""Render all predeclared challenger results without ranking or selecting models."""
import argparse
import json
from pathlib import Path

import pandas as pd


def table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---']*len(headers)) + ' |'] + [
        '| ' + ' | '.join(str(v) for v in row) + ' |' for row in rows]


def number(value, percent=False):
    return '—' if value is None else f'{value*100:.2f}%' if percent else f'{value:.4f}'


def render(source: Path, output: Path):
    report = json.loads(source.read_text())
    previous = json.loads(Path('artifacts/stage5_comparison_640b3940.json').read_text())['runs']
    lines = ['# Stage 6 frozen GRU challenger research', '',
             'This is a fixed-configuration research comparison, not evidence of future profitability. '
             'No parameters or thresholds were selected from these outcomes.', '',
             'Primary protocol: 49 daily features, sequence length 64, binary '
             '`log(Close[t+5]/Close[t]) > 0.002`, probability cutoff 0.5. '
             'Nine original folds per run; no warm start. Each test set contains 2,250 matched origins. '
             'Baseline OOF probabilities are copied from checksum-validated Stage 4 artifacts, not retrained.', '',
             'Economics: existing Stage 5 LONG/FLAT engine; enter next Open, exit five bars later; '
             'one non-overlapping position, no same-open re-entry; 1 bp commission and 1 bp slippage per side. '
             'Buy & Hold uses identical dates and costs. Overlapping five-day labels are dependent observations.', '',
             f'Machine-readable experiment: `{source}`', '']
    for name, run in report['runs'].items():
        wf, bt = run['walk_forward'], run['backtest']
        root = Path(wf['artifact_path'])
        folds = json.loads((root/'folds.json').read_text())
        lines += [f'## {name.replace("_", " ")}', '',
                  f'OOF: {wf["oof_rows"] if "oof_rows" in wf else len(pd.read_csv(root/"oof_predictions.csv"))} matched rows; '
                  f'{wf["raw_candles"]} frozen candles. Evaluation: '
                  f'{bt["net"]["evaluation_start"][:10]} to {bt["net"]["evaluation_end"][:10]}.', '']
        rows, economic = [], []
        for model in ('majority','training_prior','momentum','logistic','xgboost','gru'):
            ml = wf['models'][model]['oof']
            old = next((r for r in previous if r['source']==name and r['scenario']=='primary' and r['model']==model), None)
            metrics = bt['net'] if model=='gru' else old['net'] if old else None
            rows.append([model] + [number(ml[k]) for k in ('roc_auc','pr_auc','brier_score','log_loss','accuracy','balanced_accuracy','f1')] +
                        [number(metrics['sharpe']) if metrics else '—'])
            if metrics:
                economic.append([model] + [number(metrics[k], k in ('total_return','cagr','max_drawdown','exposure')) for k in
                                          ('total_return','cagr','sharpe','sortino','max_drawdown','calmar','profit_factor')] +
                                [metrics['trade_count'],number(metrics['exposure'],True),f"${metrics['total_costs']:,.2f}"])
        bh = bt['benchmarks']['buy_hold']
        rows.append(['Buy & Hold'] + ['—']*7 + [number(bh['sharpe'])])
        lines += table(['Model','OOF ROC-AUC','PR-AUC (AP)','Brier','Log loss','Accuracy','Balanced accuracy','F1','Net Sharpe'], rows) + ['']
        economic.append(['Buy & Hold'] + [number(bh[k], k in ('total_return','cagr','max_drawdown')) for k in
                                          ('total_return','cagr','sharpe','sortino','max_drawdown','calmar','profit_factor')] +
                        [bh['trade_count'],number(bh['exposure'],True),f"${bh['total_costs']:,.2f}"])
        lines += table(['Model','Net return','CAGR','Sharpe','Sortino','Max DD','Calmar','Profit factor','Trades','Exposure','Costs'],economic) + ['']
        wins=sum(f['models']['gru']['test']['roc_auc']>f['models']['xgboost']['test']['roc_auc'] for f in folds)
        above=wf['models']['gru']['auc_above_half']['count']
        lines += [f'GRU ROC-AUC beats XGBoost in **{wins}/{len(folds)}** matched folds and exceeds 0.50 in '
                  f'**{above}/{len(folds)}**. Training prior is a probability baseline; Buy & Hold has no ML metrics.', '']
        lines += table(['GRU fold metric','Mean','Median','Std (population)','Min','Max'],[
            [key]+[number(wf['models']['gru']['aggregate'][key][s]) for s in ('mean','median','std','min','max')]
            for key in ('roc_auc','pr_auc','brier_score','log_loss','f1')]) + ['']
        lines += table(['Fold','Test start','Test end','GRU AUC','XGB AUC','GRU AP','Brier','Log loss','F1','Best/final epoch','Seconds'],[
            [f['fold_id'],f['partitions']['test']['origin_start'][:10],f['partitions']['test']['origin_end'][:10],
             number(f['models']['gru']['test']['roc_auc']),number(f['models']['xgboost']['test']['roc_auc'])] +
            [number(f['models']['gru']['test'][k]) for k in ('pr_auc','brier_score','log_loss','f1')] +
            [f"{f['models']['gru']['training_diagnostics']['best_epoch']}/{f['models']['gru']['training_diagnostics']['epochs_trained']}",
             number(f['models']['gru']['training_diagnostics']['training_seconds'])] for f in folds]) + ['']
        p=wf['models']['gru']['probabilities']
        lines += table(['Min','Q25','Median','Q75','Max','Mean','Std','Fraction >= 0.5'],[
            [number(p[k]) for k in ('min','q25','median','q75','max','mean','std','predicted_up_rate')]]) + ['']
        diagnostics=[f['models']['gru']['training_diagnostics'] for f in folds]
        lines += [f"Training total: {sum(d['training_seconds'] for d in diagnostics):.2f} seconds. "
                  f"Device(s): {sorted(set(d['device'] for d in diagnostics))}. "
                  f"Trainable parameters: {diagnostics[0]['parameter_count']}. "
                  f"Final validation loss > train loss + 0.15 in {sum(d['overfit_warning'] for d in diagnostics)}/{len(folds)} folds. "
                  f"Final validation loss > best loss + 0.02 in {sum(d['validation_worse_than_best'] for d in diagnostics)}/{len(folds)} folds.", '',
                  f"Test inference per fold (250 sequences): {[round(f['models']['gru']['inference_ms'],2) for f in folds]} ms. "
                  'These timings include batched network inference and device transfers; feature construction/scaling are excluded.', '']
        buckets=pd.read_csv(Path(bt['artifact_path'])/'probability_buckets.csv')
        lines += ['Probability buckets are descriptive calibration/outcome diagnostics, not calibrated predictions or trading returns.', '']
        lines += table(list(buckets.columns), buckets.fillna('—').round(5).values.tolist()) + ['']
        lines += [f'Walk-forward artifact: `{root}`', '', f'Backtest artifact: `{bt["artifact_path"]}`', '']
    smoke=report.get('single_split')
    if smoke:
        lines += ['## Single-split smoke test', '', f'Artifact: `{smoke["report_path"]}`. '
                  'This separate 70/15/15 run retrains the baseline models solely for the requested smoke comparison. '
                  'It does not replace any frozen walk-forward baseline.', '']
        lines += table(['Model','Test AUC','Test AP','Brier','Log loss','Accuracy','Balanced accuracy','F1'],[
            [name]+[number(value['test'][key]) for key in ('roc_auc','pr_auc','brier_score','log_loss','accuracy','balanced_accuracy','f1')]
            for name,value in smoke['models'].items()]) + ['']
    lines += ['## Limits', '',
              'The GRU is a challenger, not an automatically preferred model. Thirty-six fold fits on two related US assets '
              'do not constitute independent replication. Current results must not be used to choose a new threshold, '
              'architecture or feature set and then described as unseen evidence.', '',
              'The binary model cannot supply a valid exact-price forecast. The Stage 6 legacy-model migration blocker '
              'was resolved in Stage 6.5; see `docs/stage65-migration.md`. No ensemble was built.', '']
    output.write_text('\n'.join(lines))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path,default=Path('docs/stage6-research.md'))
    args=parser.parse_args()
    render(args.source,args.output)
