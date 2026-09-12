"""Real CPU PyTorch checks, causal sequence fixtures, and shared-pipeline integration."""
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler
import torch

from stock_app.config import GRUConfig, TargetConfig, WalkForwardConfig
from stock_app.models.gru_model import GRUClassifier, GRUNetwork
from stock_app.models.torch_utils import get_torch_device
from stock_app.models.registry import load_model, model_contract, save_model
from stock_app.training.sequence_dataset import SequenceDataset, SequenceInput
from stock_app.training.feature_dataset import build_feature_dataset
from stock_app.training.model_inputs import model_partition
from stock_app.training.folds import generate_folds, dataset_for_fold
from stock_app.training.walk_forward import run_walk_forward
from stock_app.targets.target_builder import build_targets
from research_data import synthetic_market

SMALL = GRUConfig(sequence_length=8, projection_size=8, hidden_size=8, head_size=8, num_layers=1,
                  dropout=0, batch_size=64, max_epochs=3, patience=2, device='cpu', cpu_threads=1)


def sequence_fixture(signal=True, count=1200):
    rng = np.random.default_rng(943)
    index = pd.date_range('2010-01-01', periods=count, freq='B')
    pulse = rng.normal(size=count)
    history = pd.DataFrame({'pulse': pulse, 'noise': rng.normal(size=count)}, index=index)
    # The current feature alone is independent; four prior pulses determine t+1.
    past = pd.Series(pulse).shift(1).rolling(4).mean().fillna(0).to_numpy()
    outcomes = np.where(past > 0, .02, -.02) if signal else rng.choice([-.02,.02], size=count)
    prices = pd.Series(100*np.exp(np.r_[0, outcomes[:-1]].cumsum()), index=index)
    targets = build_targets(prices, TargetConfig(task='binary'))
    return history, targets


def fixture_inputs(signal=True):
    history, target = sequence_fixture(signal)
    train = SequenceInput(history, history.index[8:700], 8, history.iloc[:700])
    val = SequenceInput(history, history.index[705:900], 8)
    test = SequenceInput(history, history.index[905:1199], 8)
    return train, target.target.loc[train.index], val, target.target.loc[val.index], test, target.target.loc[test.index]


def test_torch_device_selection(monkeypatch):
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: True)
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: True)
    assert str(get_torch_device()) == 'cuda'
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    assert str(get_torch_device()) == 'mps'
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: False)
    assert str(get_torch_device()) == 'cpu'
    with pytest.raises(ValueError):
        get_torch_device('mps')


@pytest.mark.parametrize('length', [1,8,64])
@pytest.mark.parametrize('horizon', [1,5,20])
def test_sequence_alignment_target_and_split_context(length, horizon):
    raw = synthetic_market(False, rows=800)
    dataset = build_feature_dataset(raw, target_config=TargetConfig(horizon=horizon, task='binary'))
    model = GRUClassifier(replace(SMALL, sequence_length=length))
    for partition in ('train','validation','test'):
        X, y = model_partition(model, dataset, partition)
        scaler = StandardScaler().fit(dataset.partition('train')[0])
        sequences = SequenceDataset(X, scaler, y)
        sample, label = sequences[0]
        pos = X.positions[0]
        assert X.history.index[pos] == X.index[0]
        assert dataset.target_times.loc[X.index[0]] == raw.index[pos+horizon]
        expected = scaler.transform(X.history.iloc[pos-length+1:pos+1]).astype(np.float32)
        np.testing.assert_array_equal(sample.numpy(), expected)
        assert label.item() == y.iloc[0]
        if partition != 'train' and length > 1:
            assert X.history.index[pos-length+1] < X.index[0]


def test_sequence_no_future_data_or_invalid_gap():
    X,y,V,v,T,t = fixture_inputs()
    scaler = StandardScaler().fit(X.scaler_rows)
    before = SequenceDataset(X, scaler, y)[-1][0].numpy()
    changed = X.history.copy()
    changed.iloc[700:] *= 1000
    after = SequenceDataset(replace(X, history=changed), scaler, y)[-1][0].numpy()
    np.testing.assert_array_equal(before, after)
    changed.iloc[699] = np.nan
    with pytest.raises(ValueError, match='invalid rows'):
        replace(X, history=changed)


def test_gru_forward_shape_and_parameter_count():
    model = GRUNetwork(49, GRUConfig())
    assert model(torch.zeros(3,64,49)).shape == (3,)
    assert sum(p.numel() for p in model.parameters() if p.requires_grad) == 57473
    assert not any(isinstance(m, torch.nn.Sigmoid) for m in model.modules())


def test_train_scaler_unique_rows_and_future_outlier_invariance():
    X,y,V,v,T,t = fixture_inputs()
    model = GRUClassifier(SMALL).fit(X,y,validation=(V,v))
    assert model.scaler.n_samples_seen_ == 700
    np.testing.assert_allclose(model.scaler.mean_, X.scaler_rows.mean())
    earlier = model.predict_proba(X)
    changed = X.history.copy()
    changed.iloc[700:] *= 10000
    other = GRUClassifier(SMALL).fit(replace(X,history=changed), y, validation=(replace(V,history=changed),v))
    np.testing.assert_array_equal(model.scaler.mean_, other.scaler.mean_)
    np.testing.assert_array_equal(model.scaler.var_, other.scaler.var_)
    # Selection may change with validation outcomes/inputs; fixed fitted inference must not.
    np.testing.assert_array_equal(earlier,model.predict_proba(replace(X,history=changed)))
    p = model.predict_proba(T)
    assert p.shape == (len(T),) and np.isfinite(p).all() and ((p>=0)&(p<=1)).all()


def test_gru_early_stopping_and_gradient_clipping(monkeypatch):
    X,y,V,v,T,t = fixture_inputs()
    norms=[]
    original=torch.nn.utils.clip_grad_norm_
    def recording(parameters, max_norm, **kwargs):
        parameters=list(parameters)
        out=original(parameters,max_norm,**kwargs)
        actual=torch.sqrt(sum((p.grad**2).sum() for p in parameters if p.grad is not None)).item()
        norms.append(actual)
        return out
    monkeypatch.setattr(torch.nn.utils,'clip_grad_norm_',recording)
    cfg=replace(SMALL,max_epochs=10,patience=2,min_delta=10,gradient_clip=.05)
    model=GRUClassifier(cfg).fit(X,y,validation=(V,v))
    assert model.training_diagnostics['best_validation_loss']==min(row['validation_loss'] for row in model.history)
    assert model.training_diagnostics['epochs_trained']==3
    assert max(norms) <= .05001
    assert sum(row['clipped_batches'] for row in model.history)>0


def test_gru_save_load_real_cpu_and_metadata_mismatch(tmp_path):
    raw=synthetic_market(False,rows=800)
    dataset=build_feature_dataset(raw,target_config=TargetConfig(horizon=5,task='binary'))
    model=GRUClassifier(SMALL)
    X,y=model_partition(model,dataset,'train')
    V,v=model_partition(model,dataset,'validation')
    T,t=model_partition(model,dataset,'test')
    model.fit(X,y,validation=(V,v))
    expected=model.predict_proba(T)
    contract=model_contract(model,dataset,'TEST',{})
    path=save_model(model,tmp_path/'gru',contract,{}, {})
    loaded=load_model(path,contract,device='cpu')
    np.testing.assert_allclose(loaded.predict_proba(T),expected,rtol=1e-6,atol=1e-7)
    assert (path/'gru.pt').exists() and (path/'scaler.pkl').exists()
    for key,value in [('sequence_length',9),('feature_names',list(reversed(contract['feature_names']))),
                       ('target', {'task':'binary','horizon':20,'threshold':.002})]:
        with pytest.raises(ValueError,match='fingerprint'):
            load_model(path,{**contract,key:value})
    with (path/'scaler.pkl').open('ab') as handle:
        handle.write(b'changed')
    with pytest.raises(ValueError,match='checksum'):
        load_model(path,contract)


@pytest.mark.parametrize('signal',[True,False])
def test_gru_synthetic_sequence_signal_and_noise(signal):
    X,y,V,v,T,t=fixture_inputs(signal)
    cfg=replace(SMALL,hidden_size=16,projection_size=16,head_size=16,learning_rate=.01,max_epochs=20,patience=5)
    model=GRUClassifier(cfg).fit(X,y,validation=(V,v))
    auc=roc_auc_score(t,model.predict_proba(T))
    print('GRU_SYNTHETIC',json.dumps({'signal':signal,'auc':auc,'best_epoch':model.training_diagnostics['best_epoch'],
                                    'train_first':model.history[0]['train_loss'],'train_last':model.history[-1]['train_loss']}))
    if signal:
        assert auc>.8
        assert model.history[-1]['train_loss']<model.history[0]['train_loss']
    else:
        assert .35<auc<.65


def test_gru_walk_forward_fresh_models_scalers_oof_and_backtest(tmp_path):
    from stock_app.backtest.inputs import load_frozen_inputs
    from stock_app.backtest.run import run_backtest
    made=[]
    def factory():
        m=GRUClassifier(replace(SMALL,max_epochs=1))
        made.append(m)
        return m
    raw=synthetic_market(False,rows=1000)
    cfg=WalkForwardConfig(minimum_train_samples=400,rolling_train_samples=400,validation_samples=100,test_samples=100,step_samples=100,max_folds=3)
    result=run_walk_forward(raw,'SYNTH',tmp_path,cfg,target_config=TargetConfig(horizon=5,task='binary'),model_factories={'gru':factory})
    assert len(made)==3 and len({id(m.network) for m in made})==3
    assert len({id(m.scaler) for m in made})==3
    assert not np.array_equal(made[0].scaler.mean_,made[1].scaler.mean_)
    source=load_frozen_inputs(Path(result['artifact_path']),price_convention='auto_adjusted_ohlc')
    assert source.oof.index.is_unique and source.oof.index.is_monotonic_increasing
    assert len(source.oof)==300
    folds=generate_folds(raw.index,5,cfg)
    np.testing.assert_array_equal(source.oof.raw_position,np.concatenate([f.split.test for f in folds]))
    bt=run_backtest(source,'gru',output=tmp_path/'backtest')
    assert bt['net']['sessions']>0


def test_gru_future_fold_outliers_do_not_change_earlier_fitted_predictions(tmp_path):
    raw=synthetic_market(False,rows=1000)
    cfg=WalkForwardConfig(minimum_train_samples=400,rolling_train_samples=400,validation_samples=100,test_samples=100,step_samples=100,max_folds=1)
    factory={'gru':lambda:GRUClassifier(replace(SMALL,max_epochs=1))}
    a=run_walk_forward(raw,'SYNTH',tmp_path,cfg,target_config=TargetConfig(horizon=5,task='binary'),model_factories=factory)
    changed=raw.copy()
    changed.iloc[750:] *= 1000
    b=run_walk_forward(changed,'SYNTH',tmp_path,cfg,target_config=TargetConfig(horizon=5,task='binary'),model_factories=factory)
    pa=pd.read_csv(Path(a['artifact_path'])/'oof_predictions.csv')
    pb=pd.read_csv(Path(b['artifact_path'])/'oof_predictions.csv')
    pd.testing.assert_frame_equal(pa,pb)
    fa=json.loads((Path(a['artifact_path'])/'folds.json').read_text())[0]
    fb=json.loads((Path(b['artifact_path'])/'folds.json').read_text())[0]
    assert fa['models']['gru']['fingerprint']==fb['models']['gru']['fingerprint']
