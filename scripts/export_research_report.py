"""Export actual completed local experiments without fetching or retraining."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stock_app.research.runtime import get_runtime
from stock_app.research.store import utcnow
from stock_app.research.statistics import equal_weight_aggregate


def export(destination):
    runtime = get_runtime()
    def snapshot(ticker, identity):
        runtime.data.load(identity)  # Verify both source and adjusted checksums.
        metadata = next(m for m in runtime.data.list_snapshots(ticker) if m['id'] == identity)
        return {k:metadata.get(k) for k in ['id','sha256','raw_sha256','first_session','last_session',
                                          'created_at','rows','source','adjustment']}
    selected = {}
    for job in runtime.store.list('jobs', state='completed'):
        if job['kind'] == 'experiment' and job.get('progress', {}).get('aggregate'):
            selected.setdefault((job['symbol'], job['parameters']['task']), job)
    records = []
    for (symbol, task), job in sorted(selected.items()):
        report = job['progress']
        reference = 'training_prior' if task == 'binary' else 'unchanged_price'
        metric = 'brier' if task == 'binary' else 'mae'
        baseline = statistics.mean(f['baselines'][reference][metric] for f in report['folds'])
        path = runtime.root / 'experiments' / job['id'] / 'report.json'
        feature_path = path.parent / 'feature-comparison-v1.json'
        record = {'symbol':symbol, 'task':task, 'job_id':job['id'], 'finished_at':job['finished_at'],
                  'report_path':str(path.relative_to(runtime.root)),
                  'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                  'aggregate':report['aggregate'], 'baseline':reference, 'baseline_error':baseline,
                  'primary_error':report['aggregate'][metric], 'baseline_improvement':baseline-report['aggregate'][metric],
                  'origins':sum(f['metrics']['n'] for f in report['folds']),
                  'evaluation_start':report['folds'][0]['boundaries']['test']['origin_start'],
                  'evaluation_end':report['folds'][-1]['boundaries']['test']['origin_end'],
                  'candidate':{key:report['candidate'][key] for key in
                               ['artifact_sha256','active_cutoff','feature_set','selection','independent_evaluation']},
                  'snapshots':{ticker:snapshot(ticker,identity) for ticker,identity in job['snapshots'].items()},
                  'folds':[{k:f[k] for k in ['fold','selection','boundaries','metrics','baselines','comparisons']} for f in report['folds']]}
        if feature_path.exists():
            comparison = json.loads(feature_path.read_text())
            record['feature_comparison'] = {k:comparison[k] for k in
                ['schema_version','status','aggregate','source_report_sha256','selection_rule']}
            record['feature_comparison']['folds'] = [
                {'fold':f['fold'], 'paired_loss':f['feature_comparison']['paired_loss'],
                 'metrics':{name:v['metrics'] for name,v in f['per_feature'].items()}}
                for f in comparison['folds'] if f.get('feature_comparison')]
        records.append(record)
    result = {'generated_at':utcnow(), 'evaluation_kind':'historical_replay',
              'warning':'Research comparisons on inspected history, not untouched confirmation or prospective skill.',
              'records':records,
              'equal_weight':{task:equal_weight_aggregate([r['aggregate'] for r in records if r['task']==task],
                 ['brier','log_loss','auc','balanced_accuracy'] if task=='binary' else ['mae','rmse','interval_coverage'])
                 for task in ['binary','regression']}}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.with_suffix('.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    dates = sorted({r['evaluation_start'][:10] for r in records} | {r['evaluation_end'][:10] for r in records})
    observations = sorted({r['origins'] for r in records})
    downloads = sorted({m['created_at'][:10] for r in records for m in r['snapshots'].values()})
    not_beaten = sum(r['baseline_improvement'] <= 0 for r in records)
    active = runtime.store.list('models',state='active')
    legacy_count = sum(bool(m.get('metadata',{}).get('legacy')) for m in active)
    shadow_count = len(runtime.store.list('models',state='shadow'))
    lines = ['# Initial local research results', '', f"Exported {result['generated_at']} from completed local jobs.", '',
             '**These are historical research comparisons, not evidence of prospective forecasting skill.**', '',
             'The first full run benchmarked AAPL before expanding to SPY, NVDA and TSLA. '+
             'The table uses the latest completed run for each ticker/task; earlier AAPL runs remain preserved.', '',
             f"Evaluation origins span {dates[0] if dates else 'unavailable'} through {dates[-1] if dates else 'unavailable'}; "+
             f"resolved origins per comparison: {', '.join(map(str,observations))}. Exact fold boundaries and target dates appear in the JSON. "+
             f"Input download dates: {', '.join(downloads)}. "+
             'They are immutable research inputs, not a claim about the latest available live price.', '',
             '## Probability forecasts', '', 'Lower Brier score is better; the comparator predicts the fitting-window event frequency.', '',
             '| Ticker | Model Brier | Training-prior Brier | ROC-AUC | Result |', '|---|---:|---:|---:|---|']
    for r in records:
        if r['task']=='binary':
            a=r['aggregate'];lines.append(f"| {r['symbol']} | {a['brier']:.6f} | {r['baseline_error']:.6f} | {a['auc']:.4f} | {'Baseline not beaten' if r['baseline_improvement']<=0 else 'Historical improvement; prospective evidence required'} |")
    lines += ['', '## Return forecasts', '', 'MAE is in log-return units. The baseline predicts zero return (unchanged adjusted Close).', '',
              '| Ticker | Model return MAE | Unchanged-price MAE | Price MAE (USD) | Nominal 80% range coverage |', '|---|---:|---:|---:|---:|']
    for r in records:
        if r['task']=='regression':
            a=r['aggregate'];lines.append(f"| {r['symbol']} | {a['mae']:.6f} | {r['baseline_error']:.6f} | {a['price_mae']:.2f} | {a['interval_coverage']:.1%} |")
    lines += ['', '## Original versus market-context features', '',
              'Each feature set selects its own training window/settings using validation only. '+
              'Both are evaluated on matched outer dates. Lower is better; these are historical comparisons.', '',
              '| Ticker | Task | Original 49 features | Context features |', '|---|---|---:|---:|']
    for r in records:
        comparison = r.get('feature_comparison', {}).get('aggregate', {})
        if 'baseline' in comparison and 'context' in comparison:
            metric = 'brier' if r['task']=='binary' else 'mae'
            lines.append(f"| {r['symbol']} | {r['task']} {metric} | {comparison['baseline'][metric]:.6f} | {comparison['context'][metric]:.6f} |")
    lines += ['', '## Interpretation', '',
              f'{not_beaten} of {len(records)} selected procedures did not beat their simple comparator on average primary error. '+
              'Historical results alone do not qualify candidates for activation. Some individual folds can look favorable without establishing a consistent advantage. '+
              'Nominal range coverage is a target; measured coverage varies by ticker and period.', '',
              f'At export: {legacy_count} legacy active models, {len(active)-legacy_count} research active models and {shadow_count} frozen shadow candidates. '+
              'Qualification requires at least 126 matched resolved prospective origins '+
              'and positive paired confidence bounds before a user can activate a candidate.', '',
              'Equal-weight cross-symbol results (no pooled dollar errors):', '', '```json',json.dumps(result['equal_weight'],indent=2),'```', '',
              '## Reproducibility and evidence', '',
              'The accompanying JSON includes job identifiers, checksums, exact input snapshot identifiers, fold boundaries, '+
              'all baseline metrics and paired bootstrap confidence intervals. Full observations, fitting checkpoints and immutable model '+
              'files remain under the ignored local research artifact directory. Preserve that directory with a SQLite backup.', '',
              'Feature-comparison addenda, when present, evaluate each feature configuration on matched held-out origins. '+
              'They preserve the original report and model bytes; inspecting these periods still makes them research comparisons.', '',
              '```bash', 'python scripts/export_research_report.py', '```', '',
              'See [platform operations](research-platform.md) for backup, offline replay, recovery and rollback. '+
              'Manual GRU support is tested separately; a full live-data GRU run was not included in these tabular results.', '']
    destination.with_suffix('.md').write_text('\n'.join(lines))
    print(f'Exported {len(records)} comparisons to {destination.with_suffix(".md")}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('docs/research-initial-results'))
    export(parser.parse_args().output)
