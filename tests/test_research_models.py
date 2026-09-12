import json

import numpy as np
import pytest
from pandas.testing import assert_frame_equal
from xgboost import XGBClassifier

from stock_app.config import DEFAULT_SPLIT, TargetConfig, XGBoostConfig
from stock_app.models import LogisticClassifier, MajorityClassifier, MomentumClassifier, PriorClassifier, XGBoostClassifier
from stock_app.models.base import class_balance
from stock_app.models.registry import load_model, model_contract, save_model, fingerprint
from stock_app.training.feature_dataset import build_feature_dataset
from dataclasses import asdict
from test_features import candles


@pytest.fixture
def data(candles):
    return build_feature_dataset(candles, target_config=TargetConfig(task="binary", horizon=5))


def test_majority_baseline_uses_train_only(data):
    X, y = data.partition("train")
    model = MajorityClassifier().fit(X, y)
    assert model.positive_probability == float(y.mean() > .5)
    assert model.balance == class_balance(y)
    before = model.predict_proba(data.partition("test")[0])
    data.y.loc[data.partition("test")[0].index] = 1
    np.testing.assert_array_equal(before, model.predict_proba(data.partition("test")[0]))


def test_prior_and_momentum_definitions(data):
    X, y = data.partition("train")
    test, _ = data.partition("test")
    np.testing.assert_allclose(PriorClassifier().fit(X, y).predict_proba(test), y.mean())
    np.testing.assert_array_equal(MomentumClassifier().fit(X, y).predict_proba(test), (test.return_1 > 0).astype(float))


def test_logistic_scaler_fit_train_only(data, monkeypatch):
    X, y = data.partition("train")
    model = LogisticClassifier().fit(X, y)
    scaler = model.estimator.named_steps["standardscaler"]
    assert scaler.n_samples_seen_ == len(X)
    np.testing.assert_allclose(scaler.mean_, X.mean(axis=0))
    np.testing.assert_allclose(scaler.var_, X.var(axis=0, ddof=0))
    def forbidden(*args, **kwargs):
        raise AssertionError("Inference refitted scaler")
    monkeypatch.setattr(type(scaler), "fit", forbidden)
    test, _ = data.partition("test")
    assert np.isfinite(model.predict_proba(test * 1000)).all()
    np.testing.assert_allclose(scaler.mean_, X.mean(axis=0))


@pytest.mark.parametrize("kind", ["majority", "prior", "momentum", "logistic", "xgboost"])
def test_probability_shape_bounds_and_feature_order(data, kind):
    X, y = data.partition("train")
    val = data.partition("validation")
    test, _ = data.partition("test")
    model = {"majority": MajorityClassifier, "prior": PriorClassifier,
             "momentum": MomentumClassifier, "logistic": LogisticClassifier,
             "xgboost": lambda: XGBoostClassifier(XGBoostConfig(n_estimators=12, early_stopping_rounds=3))}[kind]()
    if kind == "xgboost":
        model.fit(X, y, validation=val)
    else:
        model.fit(X, y)
    p = model.predict_proba(test)
    assert p.shape == (len(test),)
    assert np.isfinite(p).all() and ((0 <= p) & (p <= 1)).all()
    with pytest.raises(ValueError, match="names/order"):
        model.predict_proba(test[list(reversed(test.columns))])
    with pytest.raises(ValueError, match="names/order"):
        model.predict_proba(test.drop(columns=test.columns[-1]))


def test_xgboost_no_test_fit_and_validation_usage(data, monkeypatch):
    train, y = data.partition("train")
    validation, y_val = data.partition("validation")
    captured = {}
    original = XGBClassifier.fit
    def record(self, X, labels, **kwargs):
        captured.update(X=X.copy(), y=labels.copy(), eval_set=kwargs["eval_set"])
        return original(self, X, labels, **kwargs)
    monkeypatch.setattr(XGBClassifier, "fit", record)
    model = XGBoostClassifier(XGBoostConfig(n_estimators=20, early_stopping_rounds=3))
    model.fit(train, y, validation=(validation, y_val))
    assert_frame_equal(captured["X"], train)  # unscaled values
    assert len(captured["eval_set"]) == 1
    assert_frame_equal(captured["eval_set"][0][0], validation)
    assert not captured["X"].index.intersection(data.partition("test")[0].index).size
    assert set(model.estimator.evals_result()) == {"validation_0"}
    assert 0 <= model.estimator.best_iteration < 20
    with pytest.raises(ValueError, match="validation required"):
        model.fit(train, y)
    with pytest.raises(ValueError, match="strictly after"):
        model.fit(train, y, validation=(train, y))


def test_class_balance_train_only(data):
    X, y = data.partition("train")
    model = XGBoostClassifier(XGBoostConfig(n_estimators=10, early_stopping_rounds=2, balance_classes=True))
    model.fit(X, y, validation=data.partition("validation"))
    assert model.scale_pos_weight == pytest.approx((y == 0).sum() / (y == 1).sum())


@pytest.mark.parametrize("kind", ["majority", "prior", "momentum", "logistic", "xgboost"])
def test_model_save_load_metadata_and_fingerprint(data, tmp_path, kind):
    X, y = data.partition("train")
    model = {"majority": MajorityClassifier, "prior": PriorClassifier,
             "momentum": MomentumClassifier, "logistic": LogisticClassifier,
             "xgboost": lambda: XGBoostClassifier(XGBoostConfig(n_estimators=10, early_stopping_rounds=2))}[kind]()
    if kind == "xgboost":
        model.fit(X, y, validation=data.partition("validation"))
    else:
        model.fit(X, y)
    contract = model_contract(model, data, "TEST", asdict(DEFAULT_SPLIT))
    path = save_model(model, tmp_path / kind, contract, {"test": {"roc_auc": .5}})
    loaded = load_model(path, contract)
    test, _ = data.partition("test")
    np.testing.assert_allclose(loaded.predict_proba(test), model.predict_proba(test), atol=1e-7)
    metadata = json.loads((path / "metadata.json").read_text())
    assert metadata["contract"]["target"]["horizon"] == 5
    assert metadata["contract"]["feature_config"]["sma_windows"][-1] == 200
    assert metadata["contract"]["fit_dates"]["train"]["samples"] == len(X)
    assert metadata["fingerprint"] == fingerprint(contract)
    altered = json.loads(json.dumps(contract))
    altered["feature_names"] = list(reversed(altered["feature_names"]))
    with pytest.raises(ValueError, match="fingerprint"):
        load_model(path, altered)
    altered = json.loads(json.dumps(contract))
    altered["feature_config"]["rsi_window"] = 7
    with pytest.raises(ValueError, match="fingerprint"):
        load_model(path, altered)
    (path / metadata["model_file"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        load_model(path, contract)


def test_fingerprint_deterministic_excludes_test_observations(data):
    model = LogisticClassifier().fit(*data.partition("train"))
    first = model_contract(model, data, "AAPL", asdict(DEFAULT_SPLIT))
    data.X.loc[data.partition("test")[0].index] *= 100
    data.y.loc[data.partition("test")[0].index] = 0
    assert fingerprint(first) == fingerprint(model_contract(model, data, "AAPL", asdict(DEFAULT_SPLIT)))
    assert fingerprint(first) == fingerprint(dict(reversed(list(first.items()))))
    for key, value in [("model_type", "different"), ("target", {"horizon": 20}), ("model_parameters", {"C": 10})]:
        assert fingerprint({**first, key: value}) != fingerprint(first)


def test_single_class_train_rejected_by_learned_models(data):
    X, y = data.partition("train")
    y[:] = 1
    for model in (LogisticClassifier(), XGBoostClassifier()):
        with pytest.raises(ValueError, match="both TRAIN"):
            model.fit(X, y)
    assert (MajorityClassifier().fit(X, y).predict_proba(X) == 1).all()


@pytest.mark.parametrize("kind", ["logistic", "xgboost"])
def test_repeated_fits_are_deterministic_and_test_values_do_not_select_rounds(data, kind):
    def fitted():
        model = LogisticClassifier() if kind == "logistic" else XGBoostClassifier(
            XGBoostConfig(n_estimators=20, early_stopping_rounds=3))
        X, y = data.partition("train")
        if kind == "xgboost":
            model.fit(X, y, validation=data.partition("validation"))
        else:
            model.fit(X, y)
        return model
    first = fitted()
    test, _ = data.partition("test")
    expected = first.predict_proba(test)
    data.X.loc[test.index] *= 100
    data.y.loc[test.index] = 1 - data.y.loc[test.index]
    second = fitted()
    np.testing.assert_array_equal(second.predict_proba(test), expected)
    if kind == "xgboost":
        assert first.estimator.best_iteration == second.estimator.best_iteration
