"""Fixed GRU challenger protocol using the original four Stage 4 snapshots/folds."""
from dataclasses import asdict
import argparse
import json
import logging
from pathlib import Path
from uuid import uuid4

from stock_app.config import FeatureConfig, GRUConfig, TargetConfig, WalkForwardConfig
from stock_app.features import build_features
from stock_app.models.gru_model import GRUClassifier
from stock_app.backtest.inputs import load_frozen_inputs
from stock_app.backtest.run import run_backtest
from stock_app.training.train_baselines import run_research
from stock_app.training.walk_forward import run_walk_forward


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison',type=Path,default=Path('artifacts/stage4_comparison_1053262b.json'))
    parser.add_argument('--output',type=Path,default=Path('artifacts'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    references=json.loads(args.comparison.read_text())['runs']
    config=GRUConfig()
    target=TargetConfig(horizon=5,threshold=.002,task='binary')
    args.output.mkdir(parents=True,exist_ok=True)
    destination=args.output/f'stage6_comparison_{uuid4().hex[:8]}.json'
    report={'protocol':{'gru':asdict(config),'target':asdict(target),'features':49,'decision_threshold':.5,
                        'backtest':'Existing Stage 5 engine, five-bar nonoverlap, 1bp commission + 1bp slippage/side'},'runs':{}}
    for name in ('AAPL_expanding','AAPL_rolling','SPY_expanding','SPY_rolling'):
        source=references[name]
        frozen=load_frozen_inputs(Path(source['artifact_path']),expected_fingerprint=source['configuration_fingerprint'])
        raw=frozen.market.copy()
        raw.index=raw.index.tz_convert(source['provenance']['index_timezone'])
        if frozen.configuration['target'] != asdict(target):
            raise ValueError('Original target differs from predeclared h5/.002')
        if name=='AAPL_expanding':
            smoke=run_research(raw,'AAPL',args.output,target_config=target,
                              source='Frozen Stage 4 snapshot; single-split smoke only',additional_factories=(lambda:GRUClassifier(config),))
            report['single_split']=smoke
            destination.write_text(json.dumps(report,indent=2,allow_nan=False))
        mode=source['mode']
        wf=run_walk_forward(raw,source['ticker'],args.output,WalkForwardConfig(mode=mode),FeatureConfig(),target,
                            model_factories={'gru':lambda:GRUClassifier(config)},eligible_index=build_features(raw).frame.index,
                            source='Yahoo daily full-history cache; frozen Stage 4 snapshot; GRU challenger',
                            reference_artifact=frozen.path)
        challenger=load_frozen_inputs(Path(wf['artifact_path']))
        # Original baseline probabilities are retained verbatim, not regenerated.
        backtest=run_backtest(challenger,'gru')
        report['runs'][name]={'walk_forward':wf,'backtest':backtest,'source_stage4':str(frozen.path)}
        destination.write_text(json.dumps(report,indent=2,allow_nan=False))
    print(destination.resolve())


if __name__=='__main__':
    main()
