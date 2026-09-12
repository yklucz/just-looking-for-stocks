"""Read-only audit of matched frozen rows and all 36 GRU checkpoint predictions."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import pandas as pd

from stock_app.config import GRUConfig, TargetConfig, WalkForwardConfig
from stock_app.backtest.inputs import load_frozen_inputs
from stock_app.models.gru_model import GRUClassifier
from stock_app.models.registry import load_model, model_contract
from stock_app.training.feature_dataset import build_feature_dataset
from stock_app.training.folds import dataset_for_fold, generate_folds
from stock_app.training.model_inputs import model_partition


def audit(source: Path) -> dict:
    comparison=json.loads(source.read_text())
    result={}
    for name, run in comparison['runs'].items():
        old=load_frozen_inputs(Path(run['source_stage4']))
        new=load_frozen_inputs(Path(run['walk_forward']['artifact_path']))
        assert old.oof.index.equals(new.oof.index)
        pd.testing.assert_frame_equal(old.market,new.market)
        for model in old.configuration['models']:
            for suffix in ('probability','prediction'):
                col=f'{model}_{suffix}'
                np.testing.assert_array_equal(old.oof[col],new.oof[col])
        raw=new.market.copy()
        raw.index=raw.index.tz_convert(new.summary['provenance']['index_timezone'])
        target=TargetConfig(**new.configuration['target'])
        config=WalkForwardConfig(**new.configuration['walk_forward'])
        dataset=build_feature_dataset(raw,target_config=target)
        reports=json.loads((new.path/'folds.json').read_text())
        errors=[]
        for fold, report in zip(generate_folds(raw.index,target.horizon,config),reports):
            view=dataset_for_fold(dataset,fold,dataset.features.frame.index)
            model=GRUClassifier(GRUConfig(**comparison['protocol']['gru']))
            training,_=model_partition(model,view,'train')
            rows=training.scaler_rows
            model.scaler_fit_dates={'start':rows.index[0].isoformat(),'end':rows.index[-1].isoformat(),'rows':len(rows)}
            contract=model_contract(model,view,new.configuration['ticker'],
                                    {**asdict(config),'fold_id':fold.fold_id,'train_start':fold.train_start})
            loaded=load_model(new.path/report['models']['gru']['artifact'],contract,device='cpu')
            inputs,_=model_partition(loaded,view,'test')
            actual=loaded.predict_proba(inputs)
            expected=new.oof.loc[new.oof.fold_id==fold.fold_id,'gru_probability'].to_numpy()
            np.testing.assert_allclose(actual,expected,rtol=1e-6,atol=1e-7)
            errors.append(float(np.max(np.abs(actual-expected))))
            np.testing.assert_allclose(loaded.scaler.mean_,rows.mean(),rtol=1e-12,atol=1e-12)
            assert loaded.scaler.n_samples_seen_==len(rows)
        result[name]={'matched_rows':len(new.oof),'verified_checkpoints':len(errors),
                      'maximum_reload_probability_error':max(errors),'baseline_predictions_unchanged':True,
                      'source_manifest_sha256':old.source_manifest_sha256}
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    args=parser.parse_args()
    results=audit(args.source)
    path=args.source.with_name(args.source.stem+'_verification.json')
    path.write_text(json.dumps(results,indent=2,allow_nan=False))
    print(json.dumps(results,indent=2))
